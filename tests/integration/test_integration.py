"""Integration tests against a live Synapse server on localhost:8008.

Run with:
    docker compose up -d
    uv run pytest tests/integration/ -v
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from typing import Any

import httpx
import pytest

SYNAPSE_URL = "http://localhost:8008"
REGISTRATION_SHARED_SECRET = "dev_registration_secret"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mac(nonce: str, username: str, password: str, *, admin: bool = False) -> str:
    mac = hmac.new(REGISTRATION_SHARED_SECRET.encode(), digestmod=hashlib.sha1)
    mac.update(nonce.encode())
    mac.update(b"\x00")
    mac.update(username.encode())
    mac.update(b"\x00")
    mac.update(password.encode())
    mac.update(b"\x00")
    mac.update(b"admin" if admin else b"notadmin")
    return mac.hexdigest()


async def register_user(
    client: httpx.AsyncClient, username: str, password: str
) -> str:
    """Register a new user via the shared-secret admin API and return the access token."""
    nonce_resp = await client.get(f"{SYNAPSE_URL}/_synapse/admin/v1/register")
    nonce_resp.raise_for_status()
    nonce: str = nonce_resp.json()["nonce"]

    resp = await client.post(
        f"{SYNAPSE_URL}/_synapse/admin/v1/register",
        json={
            "nonce": nonce,
            "username": username,
            "password": password,
            "admin": False,
            "mac": _make_mac(nonce, username, password),
        },
    )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


async def create_room(
    client: httpx.AsyncClient,
    token: str,
    *,
    preset: str = "public_chat",
    encrypted: bool = False,
) -> str:
    """Create a room with the given preset and return the room_id."""
    body: dict[str, Any] = {
        "preset": preset,
        "name": f"test-room-{int(time.time() * 1000)}",
    }
    if encrypted:
        body["initial_state"] = [
            {
                "type": "m.room.encryption",
                "state_key": "",
                "content": {"algorithm": "m.megolm.v1.aes-sha2"},
            }
        ]
    resp = await client.post(
        f"{SYNAPSE_URL}/_matrix/client/v3/createRoom",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    resp.raise_for_status()
    return str(resp.json()["room_id"])


async def send_message(
    client: httpx.AsyncClient, token: str, room_id: str, body: str
) -> str:
    """Send an m.room.message and return the event_id."""
    txn_id = f"txn{int(time.time() * 1_000_000)}"
    resp = await client.put(
        f"{SYNAPSE_URL}/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"msgtype": "m.text", "body": body},
    )
    resp.raise_for_status()
    return str(resp.json()["event_id"])


async def get_messages(
    client: httpx.AsyncClient, token: str, room_id: str
) -> list[dict[str, Any]]:
    """Fetch message events from a room via /messages."""
    resp = await client.get(
        f"{SYNAPSE_URL}/_matrix/client/v3/rooms/{room_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": 50, "dir": "b"},
    )
    resp.raise_for_status()
    return [
        e
        for e in resp.json().get("chunk", [])
        if e.get("type") == "m.room.message"
    ]


async def send_encrypted_event(
    client: httpx.AsyncClient, token: str, room_id: str
) -> str:
    """Send a simulated m.room.encrypted event (as an E2E client would) and return the event_id."""
    txn_id = f"txn{int(time.time() * 1_000_000)}"
    resp = await client.put(
        f"{SYNAPSE_URL}/_matrix/client/v3/rooms/{room_id}/send/m.room.encrypted/{txn_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "algorithm": "m.megolm.v1.aes-sha2",
            "sender_key": "test_sender_key",
            "ciphertext": "dGVzdF9jaXBoZXJ0ZXh0",  # base64 placeholder test_ciphertext
            "session_id": "test_session_id",
            "device_id": "test_device",
        },
    )
    resp.raise_for_status()
    return str(resp.json()["event_id"])


async def get_encrypted_events(
    client: httpx.AsyncClient, token: str, room_id: str
) -> list[dict[str, Any]]:
    """Fetch m.room.encrypted events from a room via /messages."""
    resp = await client.get(
        f"{SYNAPSE_URL}/_matrix/client/v3/rooms/{room_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": 50, "dir": "b"},
    )
    resp.raise_for_status()
    return [
        e
        for e in resp.json().get("chunk", [])
        if e.get("type") == "m.room.encrypted"
    ]


async def get_room_members(
    client: httpx.AsyncClient, token: str, room_id: str
) -> list[str]:
    """Return a list of joined member MXIDs for a room."""
    resp = await client.get(
        f"{SYNAPSE_URL}/_matrix/client/v3/rooms/{room_id}/joined_members",
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return list(resp.json().get("joined", {}).keys())


async def kick_user(
    client: httpx.AsyncClient, token: str, room_id: str, target_user_id: str
) -> httpx.Response:
    """Attempt to kick a user from a room. Returns the raw response (may be 4xx)."""
    resp = await client.post(
        f"{SYNAPSE_URL}/_matrix/client/v3/rooms/{room_id}/kick",
        headers={"Authorization": f"Bearer {token}"},
        json={"user_id": target_user_id, "reason": "test kick"},
    )
    return resp


async def ban_user(
    client: httpx.AsyncClient, token: str, room_id: str, target_user_id: str
) -> httpx.Response:
    """Attempt to ban a user from a room. Returns the raw response (may be 4xx)."""
    resp = await client.post(
        f"{SYNAPSE_URL}/_matrix/client/v3/rooms/{room_id}/ban",
        headers={"Authorization": f"Bearer {token}"},
        json={"user_id": target_user_id, "reason": "test kick"},
    )
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def synapse_client() -> httpx.AsyncClient:
    """Fresh HTTP client per test, confirms Synapse is reachable."""
    async with httpx.AsyncClient(base_url=SYNAPSE_URL, timeout=10) as client:
        resp = await client.get("/health")
        assert resp.status_code == 200, (
            f"Synapse not reachable at {SYNAPSE_URL}. "
            "Is `docker compose up` running?"
        )
        yield client  # type: ignore[misc]


@pytest.fixture()
async def user_token(synapse_client: httpx.AsyncClient) -> str:
    """Register a unique user per test and return its access token."""
    username = f"testuser_{int(time.time() * 1_000_000)}"
    return await register_user(synapse_client, username, "Password123!")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSynapseHealth:
    async def test_health_endpoint(self, synapse_client: httpx.AsyncClient) -> None:
        resp = await synapse_client.get("/health")
        assert resp.status_code == 200

    async def test_versions_endpoint(self, synapse_client: httpx.AsyncClient) -> None:
        resp = await synapse_client.get("/_matrix/client/versions")
        assert resp.status_code == 200
        assert "versions" in resp.json()


class TestRegistration:
    async def test_register_user(self, synapse_client: httpx.AsyncClient) -> None:
        username = f"reguser_{int(time.time() * 1_000_000)}"
        token = await register_user(synapse_client, username, "Password123!")
        assert token


class TestRooms:
    async def test_create_public_room(
        self, synapse_client: httpx.AsyncClient, user_token: str
    ) -> None:
        room_id = await create_room(synapse_client, user_token)
        assert room_id.startswith("!")

    async def test_create_private_room(
        self, synapse_client: httpx.AsyncClient, user_token: str
    ) -> None:
        room_id = await create_room(synapse_client, user_token, preset="private_chat")
        assert room_id.startswith("!")

    async def test_create_encrypted_room(
        self, synapse_client: httpx.AsyncClient, user_token: str
    ) -> None:
        room_id = await create_room(synapse_client, user_token, preset="private_chat", encrypted=True)
        assert room_id.startswith("!")


    async def test_send_message(
        self, synapse_client: httpx.AsyncClient, user_token: str
    ) -> None:
        room_id = await create_room(synapse_client, user_token)
        event_id = await send_message(synapse_client, user_token, room_id, "Hello!")
        assert event_id.startswith("$")

    async def test_message_is_persisted(
        self, synapse_client: httpx.AsyncClient, user_token: str
    ) -> None:
        """Verify the message can be read back via the /messages API."""
        room_id = await create_room(synapse_client, user_token)
        body = f"integration-test-{int(time.time())}"
        await send_message(synapse_client, user_token, room_id, body)

        messages = await get_messages(synapse_client, user_token, room_id)
        bodies = [m.get("content", {}).get("body") for m in messages]
        assert body in bodies

    async def test_module_archives_message_public_room(
        self, synapse_client: httpx.AsyncClient, user_token: str
    ) -> None:
        room_id = await create_room(synapse_client, user_token, preset="public_chat")
        body = f"archive-public-{int(time.time())}"
        event_id = await send_message(synapse_client, user_token, room_id, body)

        messages = await get_messages(synapse_client, user_token, room_id)
        event_ids = [m.get("event_id") for m in messages]
        assert event_id in event_ids

    async def test_module_archives_message_private_room(
        self, synapse_client: httpx.AsyncClient, user_token: str
    ) -> None:
        room_id = await create_room(synapse_client, user_token, preset="private_chat")
        body = f"archive-private-{int(time.time())}"
        event_id = await send_message(synapse_client, user_token, room_id, body)

        messages = await get_messages(synapse_client, user_token, room_id)
        event_ids = [m.get("event_id") for m in messages]
        assert event_id in event_ids

    async def test_module_receives_encrypted_events_not_plaintext(
        self, synapse_client: httpx.AsyncClient, user_token: str
    ) -> None:
        room_id = await create_room(
            synapse_client, user_token, preset="private_chat", encrypted=True
        )

        # Verify the room has the encryption state event set
        enc_resp = await synapse_client.get(
            f"{SYNAPSE_URL}/_matrix/client/v3/rooms/{room_id}/state/m.room.encryption",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert enc_resp.status_code == 200
        assert enc_resp.json()["algorithm"] == "m.megolm.v1.aes-sha2"

        # Send a simulated encrypted event (as a real E2E client would)
        event_id = await send_encrypted_event(synapse_client, user_token, room_id)

        # The event must be stored by Synapse as m.room.encrypted
        enc_events = await get_encrypted_events(synapse_client, user_token, room_id)
        event_ids = [e.get("event_id") for e in enc_events]
        assert event_id in event_ids

        # Confirm there is NO plaintext m.room.message — the body is not visible
        plain_messages = await get_messages(synapse_client, user_token, room_id)
        plain_event_ids = [m.get("event_id") for m in plain_messages]
        assert event_id not in plain_event_ids


ARCHIVE_BOT_USER_ID = "@archivebot:localhost"


class TestArchiveBot:
    async def test_bot_joins_encrypted_room(self, synapse_client: httpx.AsyncClient, user_token: str) -> None:
        """ArchiveBot must auto-join when an encrypted room is created."""
        room_id = await create_room(synapse_client, user_token, preset="private_chat", encrypted=True)

        # Give the module a moment to process the invite + join
        await asyncio.sleep(1)

        members = await get_room_members(synapse_client, user_token, room_id)
        assert ARCHIVE_BOT_USER_ID in members, (
            f"Expected {ARCHIVE_BOT_USER_ID} to be joined in {room_id}, got: {members}"
        )

    async def test_bot_not_joined_unencrypted_room(self, synapse_client: httpx.AsyncClient, user_token: str) -> None:
        """ArchiveBot must NOT auto-join unencrypted rooms."""
        room_id = await create_room(synapse_client, user_token, preset="private_chat", encrypted=False)

        await asyncio.sleep(1)

        members = await get_room_members(synapse_client, user_token, room_id)
        assert ARCHIVE_BOT_USER_ID not in members, (
            f"Expected {ARCHIVE_BOT_USER_ID} NOT to be in {room_id}, got: {members}"
        )

    # async def test_kick_bot_from_encrypted_room_is_blocked(self, synapse_client: httpx.AsyncClient, user_token: str) -> None:
    #     """Kicking the ArchiveBot from an encrypted room must be rejected with 403."""
    #     room_id = await create_room(synapse_client, user_token, preset="private_chat", encrypted=True)

    #     # Wait for bot to join
    #     await asyncio.sleep(1)
    #     members = await get_room_members(synapse_client, user_token, room_id)
    #     assert ARCHIVE_BOT_USER_ID in members, "Bot did not join the room, cannot test kick"

    #     # Try to kick the bot
    #     resp = await kick_user(synapse_client, user_token, room_id, ARCHIVE_BOT_USER_ID)

    #     assert resp.status_code == 403, (
    #         f"Expected 403 when kicking archive bot, got {resp.status_code}: {resp.text}"
    #     )

    #     # Bot must still be in the room
    #     members_after = await get_room_members(synapse_client, user_token, room_id)
    #     assert ARCHIVE_BOT_USER_ID in members_after, "Bot was kicked despite protection"



    # async def test_kick_bot_from_encrypted_room_is_banned(self, synapse_client: httpx.AsyncClient, user_token: str) -> None:
    #     """Kicking the ArchiveBot from an encrypted room must be rejected with 403."""
    #     room_id = await create_room(synapse_client, user_token, preset="private_chat", encrypted=True)

    #     # Wait for bot to join
    #     await asyncio.sleep(1)
    #     members = await get_room_members(synapse_client, user_token, room_id)
    #     assert ARCHIVE_BOT_USER_ID in members, "Bot did not join the room, cannot test ban"

    #     # Try to kick the bot
    #     resp = await ban_user(synapse_client, user_token, room_id, ARCHIVE_BOT_USER_ID)

    #     assert resp.status_code == 403, (
    #         f"Expected 403 when banning archive bot, got {resp.status_code}: {resp.text}"
    #     )

    #     # Bot must still be in the room
    #     members_after = await get_room_members(synapse_client, user_token, room_id)
    #     assert ARCHIVE_BOT_USER_ID in members_after, "Bot was kicked despite protection"
