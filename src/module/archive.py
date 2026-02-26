import logging
from typing import Any

import psycopg2
from psycopg2 import pool as psycopg2_pool
from synapse.api.constants import EventTypes, Membership
from synapse.events import EventBase
from synapse.module_api import ModuleApi
from synapse.module_api.errors import ConfigError
from synapse.types import Requester, StateMap

logger = logging.getLogger(__name__)

class ArchiveModule:
    def __init__(self, config: dict[str, Any], api: ModuleApi) -> None:
        self.api = api
        self.config = config

        self._pool: psycopg2_pool.SimpleConnectionPool | None = None

        self.api.register_third_party_rules_callbacks(
            on_new_event=self.on_new_event,
        )

    def _get_pool(self) -> psycopg2_pool.SimpleConnectionPool:
        if self._pool is None:
            db = self.config["database"]
            self._pool = psycopg2_pool.SimpleConnectionPool(
                minconn=1,
                maxconn=10,
                user=db["user"],
                password=db["password"],
                host=db["host"],
                port=db.get("port", 5432),
                database=db["database"],
            )
            self._setup_schema()
        return self._pool

    def _setup_schema(self) -> None:
        pool = self._pool
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS archived_messages (
                        id BIGSERIAL PRIMARY KEY,
                        event_id TEXT NOT NULL UNIQUE,
                        sender TEXT NOT NULL,
                        room_id TEXT NOT NULL,
                        body TEXT,
                        inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                """)
            conn.commit()
        finally:
            pool.putconn(conn)

    @staticmethod
    def parse_config(config: dict[str, Any]) -> dict[str, Any]:
        database = config.get("database")
        if database is None:
             raise ConfigError("Missing required config option: database")
         
        required_db_fields = ["user", "password", "host", "database"]
        for field in required_db_fields:
            if field not in database:
                raise ConfigError(f"Missing required database config option: {field}")

        return config

    async def on_new_event(self, event: EventBase, state_events: StateMap) -> None:
        if event.type not in [EventTypes.Message]:
            return None
        self._archive_plaintext_message(event)
        return None

    def _archive_plaintext_message(self, event: EventBase) -> None:
        pool = self._get_pool()
        conn = pool.getconn()
        try:
            content = dict(event.content)
            body = content.get("body", "")
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO archived_messages (event_id, sender, room_id, body)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (event.event_id, event.sender, event.room_id, body),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Failed to archive message")
        finally:
            pool.putconn(conn)


class ArchiveBot:
    def __init__(self, config: dict[str, Any], api: ModuleApi) -> None:
        self.api = api
        self.config = config

        self.api.register_third_party_rules_callbacks(
            on_create_room=self.on_create_room,
            on_new_event=self.on_new_event
            # check_event_allowed=self.check_event_allowed
        )

        #todo: join all existing encrypted rooms
        

    @staticmethod
    def parse_config(config: dict[str, Any]) -> dict[str, Any]:
        if "bot_user_id" not in config:
            raise ConfigError("Missing required config option: bot_user_id")
        return config

    async def on_create_room(self,requester: Requester,request_content: dict[str, Any], is_requester_admin: bool) -> None:
        bot_user_id = self.config["bot_user_id"]

        if requester.user.to_string() == bot_user_id:
            return None
        
        invite = request_content.get("invite", [])
        initial_state = request_content.get("initial_state", [])

        is_encrypted = any(state.get("type") == EventTypes.RoomEncryption for state in initial_state)

        if not is_encrypted:
            return None
        
        if bot_user_id in invite:
            return None
        
        invite.append(bot_user_id)
        request_content["invite"] = invite
        return None

    async def on_new_event(self, event: EventBase, state_events: StateMap) -> None:
        if event.type not in [EventTypes.Member]:
            return
    
        # accept the invite if the bot is invited to a room
        bot_user_id = self.config["bot_user_id"]

        if event.state_key == bot_user_id and event.content.get("membership") == Membership.INVITE:
            await self.api.update_room_membership(
                sender=bot_user_id,
                target=bot_user_id,
                room_id=event.room_id,
                new_membership=Membership.JOIN,
            )

        return None
    
    async def check_event_allowed(self, event: EventBase, state_events: StateMap) -> tuple[bool, dict | None]:
        if event.type not in [EventTypes.Member]:
            return True, None   

        bot_user_id = self.config["bot_user_id"]
        if event.state_key == bot_user_id:
            membership = event.content.get("membership")
            if membership in [Membership.BAN, Membership.LEAVE]:
                return False, None
        
        return True, None