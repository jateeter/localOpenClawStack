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
2. Generate secure local credentials:
   ```bash
   ./scripts/init-secrets.sh
   ```
3. Resolve stable releases and immutable image pins:
   ```bash
   ./scripts/update-versions.sh
   ```
4. Start the stack:
   ```bash
   ./scripts/start.sh
   ```

`start.sh` refreshes current non-prerelease versions, records immutable image
pins in `.env`, hardens the persisted gateway configuration, and verifies the
running image identities. Use `start.sh --no-update` only for an offline restart
with existing pins. WebUI credentials remain in the owner-only `.env` file and
are never committed.

## Services

- OpenClaw gateway: `http://localhost:${OPENCLAW_GATEWAY_PORT:-18789}`
- Open WebUI: `http://localhost:${OPEN_WEBUI_PORT:-8080}`

## Common commands

Start:
```bash
./scripts/start.sh
```

Verify versions, digests, health, and authentication:
```bash
./scripts/verify-deployment.sh
```

Compare two clean gateway builds for identical root filesystem layers:
```bash
./scripts/verify-image-reproduction.sh
```

Stop:
```bash
docker compose down
```

View logs:
```bash
docker compose logs -f
```

---

## RealityEngine ACP integration

OpenClaw is connected to the RealityEngine Perception Engine via the Agent
Communication Protocol (ACP) integration. The PE dispatches to OpenClaw and
receives completions back through a configured source mapping — the PE cycle
never blocks on the external agent.

### Dispatch flow

1. PE evaluates a machine trigger and assembles a `ces.terminal.event` envelope.
2. PE posts the envelope to OpenClaw:
   ```
   POST /api/integrations/acp/dispatch
   ```
   The PE returns `202 Accepted` immediately (fire-and-record). The ACP session
   runs externally without holding a PE cycle open.
3. The dispatch is recorded in the PE ledger (`GET /api/dispatch/ledger`).
4. When the OpenClaw session completes, the result is posted back to the PE:
   ```
   POST /api/integrations/completions
   ```
   with `provider: "acp"` and the configured `sourceMappingId` (default:
   `acp-openclaw-completion`). The PE resolves the mapping, updates the sensor
   source, and broadcasts state — same path as any other integration completion.

### PE environment variables

| Variable | Description |
|---|---|
| `OPENCLAW_VERSION` | OpenClaw npm package version used to build the gateway image (default: `2026.6.9`) |
| `OPENCLAW_GATEWAY_URL` | Base URL for the OpenClaw gateway (e.g. `http://localhost:18789`) |
| `ACP_SESSION_KEY` | Shared secret used when dispatching to the gateway |

### Verify

```bash
# Check integration status
curl http://localhost:5300/api/integrations/status

# Read dispatch ledger
curl http://localhost:5300/api/dispatch/ledger
```

Replace `5300` with the active PE port (`5000` for Scala, `5600` for LSP).
