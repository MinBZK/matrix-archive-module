import asyncio
import json
import logging
import os
import sys
import traceback

import aiofiles
import psycopg
from nio import AsyncClient, AsyncClientConfig, InviteEvent, LoginResponse, MatrixRoom, RoomMessageText

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Callbacks:
    def __init__(self, client: AsyncClient, aconn: psycopg.AsyncConnection) -> None:
        self.client = client
        self.aconn = aconn

    async def message_callback(self,room: MatrixRoom, event: RoomMessageText) -> None:
        if not event.decrypted:
            logger.info("Message is not decrypted, skipping")
            return None

        if event.sender == self.client.user:
            logger.info("Received message from self, ignoring")
            return None
        
        try:
            await self.aconn.execute(
                """
                INSERT INTO archived_messages (event_id, sender, room_id, body)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (event.event_id, event.sender, room.room_id, event.body),
            )
            await self.aconn.commit()
        except Exception as e:
            await self.aconn.rollback()
            logger.error(f"Failed to archive message: {e}")

    async def join_room_callback(self, room: MatrixRoom, event: InviteEvent) -> None:
        logger.info(f"Joining room {room.room_id} due to invite from {event.sender}")
        if event.sender == self.client.user:
            logger.info("Received invite from self, ignoring")
            return None
        await self.client.join(room.room_id)
        self.client.encrypt(room.room_id,"m.room.message",{"msgtype": "m.text", "body": "Hallo! Ik ben de Archive Bot. Ik ga alle berichten archiveren."})


async def main() -> None:
    logger.info("Starting Archive Bot")
    home_server = os.getenv("MATRIX_HOME_SERVER", "http://localhost:8008")
    bot_user_id = os.getenv("MATRIX_BOT_USER_ID", "@archivebot:localhost")
    bot_user_password = os.getenv("MATRIX_BOT_USER_PASSWORD", "archivebotpassword")
    store_folder = os.getenv("STORE_FOLDER","nio_store/")
    session_detail_file = os.getenv("SESSION_DETAIL_FILE", "credentials.json")

    database_user = os.getenv("DATABASE_USER")
    database_password = os.getenv("DATABASE_PASSWORD")
    database_host = os.getenv("DATABASE_HOST")
    database_name = os.getenv("DATABASE_NAME")
    database_port = os.getenv("DATABASE_PORT", "5432")

    # checking database connection before starting the client
    try:
        aconn = await psycopg.AsyncConnection.connect(
            user=database_user,
            password=database_password,
            host=database_host,
            dbname=database_name,
            port=database_port,
            autocommit=False,
        )
        logger.info("Successfully connected to the database")
    except Exception as e:
        logger.error(f"Failed to connect to the database: {e}")
        return

    # ensure the archive table exists
    try:
        await aconn.execute(
            """
            CREATE TABLE IF NOT EXISTS archived_messages (
                event_id TEXT PRIMARY KEY,
                sender TEXT NOT NULL,
                room_id TEXT NOT NULL,
                body TEXT NOT NULL,
                timestamp TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        await aconn.commit()
        logger.info("Database schema is ready")
    except Exception as e:
        await aconn.rollback()
        logger.error(f"Failed to setup database schema: {e}")
        return
    

    if store_folder and not os.path.isdir(store_folder):
        logger.info(f"Creating store folder at {store_folder}")
        os.mkdir(store_folder)

    client = None
    client_config = AsyncClientConfig(
        max_limit_exceeded=20,
        max_timeouts=20,
        backoff_factor=0.5,
        max_timeout_retry_wait_time=60,
        store_sync_tokens=True,
        encryption_enabled=True,
    )

    if not os.path.exists(session_detail_file):
        logger.info("No existing session found, logging in with password")
        client = AsyncClient(home_server, user=bot_user_id, device_id="ARCHIVEBOTDID", store_path=store_folder, config=client_config)

        resp = await client.login(bot_user_password, device_name="ARCHIVEBOTDID")

        if isinstance(resp, LoginResponse):
            logger.info(f"Logged in as {bot_user_id} device id: {client.device_id}")

            with open(session_detail_file, "w") as f:
                # write the login details to disk
                json.dump(
                    {
                        "homeserver": home_server,
                        "user_id": resp.user_id,  
                        "device_id": resp.device_id, 
                        "access_token": resp.access_token,
                    },
                    f,
                )
        else:
            logger.error(f"Failed to log in: {resp}")
            client.close()
            return
    else:
        logger.info("Existing session found, restoring from file")
        async with aiofiles.open(session_detail_file) as f:
            contents = await f.read()
        config = json.loads(contents)

        client = AsyncClient(config["homeserver"], user=config["user_id"], device_id=config["device_id"], store_path=store_folder, config=client_config)
        client.restore_login(config["user_id"], config["device_id"], config["access_token"])

        logger.info(f"Loaded session for {bot_user_id} device id: {client.device_id}")

    if client is None:
        logger.error("Failed to initialize client")
        return


    callbacks = Callbacks(client, aconn)

    client.add_event_callback(callbacks.message_callback, (RoomMessageText,))
    client.add_event_callback(callbacks.join_room_callback, (InviteEvent,))

    if client.should_upload_keys:
        await client.keys_upload()

    await client.sync_forever(timeout=30000, full_state=True)  # milliseconds


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print(traceback.format_exc())
        sys.exit(1)
    except KeyboardInterrupt:
        print("Received keyboard interrupt.")
        sys.exit(0)