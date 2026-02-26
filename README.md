# Synapse Archive System

The Archive system consists of two components. A [Synapse module](https://element-hq.github.io/synapse/latest/modules/index.html) and a `Synapse Bot` that archives all messages sent in a Matrix homeserver. The Module hooks into Synapse's event pipeline to capture unencrypted room messages and automatically joins the archive Bot into every newly created room so that bot can archive the encrypted messages.

## How It Works

```
New event
    │
    │
    │ ── ArchiveModule.on_new_event()
    │       └── m.room.message → archive plaintext message to storage backend
    │ ── ArchiveBot.on_create_room()
    |       └── type:encrypted → Add archive bot to invite list   
    │ ── ArchiveBot.on_new_event()
            └── m.room.member →  Make archive bot join room
```

1. The 2 modules are loaded by Synapse at startup.
2. On every `on_create_room` , the bot user is force-joined into the new room.
3. On every `m.room.message` event, the unencrypted message is written to the configured storage backend.

A seperate process is started for the bot user that archives all encrypted messages of the room he joined. Users can also invite this bot if it is not in the room. Once invited the bot can never be removed since we added guardrails for that.

## Quick Start (Docker)

```bash
# Clone the repo
git clone <repo-url>
cd synapse-archive-module

# Start Synapse + init services
docker compose up -d

The following accounts are created automatically on first start:
- @admin:localhost (admin user)      password: adminpassword
- @archivebot:localhost (bot user)   password: archivebotpassword
```

webviewer will be available at `http://localhost:8000`.

We added integration tests to test the feature. to start the test

```
uv sync
uv run pytest tests/integration/ -v
```

pytest will create rooms, add users and send messages. these messages will appear in the database.

To view the database goto: <http://localhost:8080>

To login use the admin credentials of pgadmin.

## Module Configuration

To enable the modules you need to download ./scr/module/archive.py to your server and enable the module by adding the following to your `homeserver.yaml`:

```yaml
modules:
  - module: archive.ArchiveModule
    config:
      database:
        user: archivemodule
        password: changethis
        host: db
        port: 5432
        database: chatarchive
  - module: archive.ArchiveBot
    config: 
      bot_user_id: "@archivebot:localhost" 
```

Make sure the module file is on the Python path. When using Docker, mount it and set `PYTHONPATH`:

```yaml
# compose.yaml excerpt
environment:
  PYTHONPATH: /modules
volumes:
  - ./src/module:/modules:ro
```

## Storage Backends

The module is designed to support multiple export targets. Planned/supported options:

| Backend | Status |
|---|---|
| PostgreSQL | ✅ Current default |
| HTTPS webhook | 🔧 Planned |
| S3 / object store | 🔧 Planned |
| Kafka | 🔧 Planned |
| RabbitMQ| 🔧 Planned |

## Development

```bash
# Install dependencies
uv sync

# Lint
uv run ruff check src/

# Type check
uv run pyright

# Run tests
uv run pytest
```

## Project Structure

```
src/
  module/
    archive.py       # Synapse module
synapse/
  conf/
    homeserver.yaml  # Local dev Synapse config
  data/              # Synapse data (sqlite, media)
  logs/.             # Synapse logs
tests/
  integration/       # Integration tests
compose.yaml         # Docker Compose for local dev
```
