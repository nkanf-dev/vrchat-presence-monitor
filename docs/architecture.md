# Architecture

```text
Browser ── VRChat login / dashboard ── FastAPI
                                          │
VRChat API ◀── per-tenant collector ──────┤
                                          ▼
                                   tenant-scoped SQLite
                                          │
                             verified local backup ── private R2

Cloudflare Tunnel ── HTTPS public route ── FastAPI
```

FastAPI owns authentication and the browser API. A successful VRChat login maps the authenticated VRChat user ID to one tenant, stores the upstream session encrypted, and issues a separate browser session. The collector manager restores active tenants on startup, polls with bounded concurrency and per-account backoff, and writes normalized snapshots, events, raw responses, profiles, and locations through the same tenant-scoped storage boundary.

SQLite runs in WAL mode on one server. The application is intentionally single-node; Docker Compose supervises the API, backup scheduler, R2 uploader and Cloudflare Tunnel. Backups use SQLite's online backup API and are independently restore-tested after upload.

The React client has no tenant selector and no credential storage. It receives only the current browser identity and that tenant's data. External collectors can still submit normalized telemetry with a scoped collector token, but hosted collection is the default product path.
