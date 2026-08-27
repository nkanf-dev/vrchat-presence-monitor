# Architecture

Presence Monitor is split into five replaceable boundaries:

```text
VRChat API / pipeline
        │
        ▼
Local monitor ── local SQLite + raw retention
        │
        │ normalized, tenant-scoped telemetry
        ▼
Hosted API ──── hosted SQLite
        │
        ▼
React viewer

hosted SQLite ── hourly verified artifact ── private R2
                                              │
                                              └─ daily downloaded restore drill

Cloudflare Tunnel terminates the public route; it is not part of collection.
```

## Local monitor

The Local process owns VRChat authentication, REST calibration, pipeline reconnects, 429 backoff and raw response retention. It listens on loopback and stores the issued session in macOS Keychain. Its browser UI and backend can run without Hosted.

## Bridge

The bridge reads only the Local HTTP projection: current friends and paginated status history. A private cursor tracks the highest exported local event ID. Events receive deterministic client IDs, so retries and resumed uploads are idempotent. The bridge never imports `vrchat_monitor.vrchat`, opens the session store, or calls VRChat.

## Hosted API

FastAPI validates bounded payloads and authenticates one of three capabilities:

- bootstrap token: create tenants and initial scoped credentials;
- collector token: append telemetry to exactly one tenant;
- browser session: read/import/export exactly one tenant.

Every query derives `tenant_id` from the authenticated database row. Request JSON cannot select a tenant. Browser session tokens and access/collector credentials are stored only as SHA-256 hashes.

## Web client

The Hosted client is a React/TypeScript/Vite single-page application. React Query owns remote cache and retry state; Zod validates every API response. Navigation uses stable hashes, dialogs use the native dialog primitive, tables retain semantic markup, and mobile navigation respects safe-area insets.

## Storage

Both current databases use SQLite WAL with foreign keys and a busy timeout. Writes are serialized inside one process. This is intentional for a single-node, small-group deployment. The `Store` API is the seam for a future PostgreSQL implementation; deploying multiple API replicas against the same SQLite volume is unsupported.

Imports are merge-only. Friend snapshots update only when their timestamps are at least as new as the stored row, and status events are idempotent. Backups use SQLite's online backup API.

The local backup scheduler produces deterministic gzip artifacts with compressed and
uncompressed SHA-256 values. A separate uploader has no database-volume mount and
writes content-addressed multipart objects through an authenticated Worker. Remote
success and downloaded restore-drill success are separate health states. R2 provides
durability, not API failover.

## Deliberate exclusions

- Hosted has no VRChat password, Cookie or encrypted-session endpoint.
- The server does not run a multi-user VRChat collector or claim that operator-controlled encryption hides sessions from the operator.
- Cloudflare D1 is not queried through its administrative REST API at request time.
- The public origin does not bind a host-wide port; Tunnel reaches the service through the Compose network.
