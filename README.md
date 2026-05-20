# localOpenClawStack

A local Docker-based deployment stack for OpenClaw (OpenClay) development.

## Prerequisites

- Docker Engine
- Docker Compose v2 (`docker compose`)

## Quick start

1. Copy environment template:
   ```bash
   cp .env.example .env
   ```
2. Edit `.env` and set a secure `OPENCLAW_GATEWAY_TOKEN`.
3. Start the stack:
   ```bash
   docker compose up -d
   ```

## Services

- OpenClaw gateway: `http://localhost:${OPENCLAW_GATEWAY_PORT:-18789}`
- Open WebUI: `http://localhost:${OPEN_WEBUI_PORT:-8080}`

## Common commands

Start:
```bash
docker compose up -d
```

Stop:
```bash
docker compose down
```

View logs:
```bash
docker compose logs -f
```
