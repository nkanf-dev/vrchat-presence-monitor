# Deployment notes

## Recommended small-group topology

One Linux host runs the Hosted API, an hourly backup scheduler, an optional R2 uploader and one Cloudflare Tunnel connector. The API binds `127.0.0.1:8080` for host diagnostics and is also reachable as `http://vrchat-monitor:8080` inside the Compose network. Only `/data`, the dedicated backup bind mount and bounded tmpfs mounts are writable.

Use a remotely managed tunnel with a tunnel-specific token file. Configure its public hostname in Cloudflare and route the origin to `http://vrchat-monitor:8080`. Tunnel uses outbound-only connections, so the host firewall does not need an inbound rule for the application.

## Why SQLite first

For a handful of tenants, status events are small, writes are sequential and a single process is easier to restore than a separate database service. The Compose stack therefore uses a named SQLite volume and an online backup command.

Do not run multiple API replicas against one SQLite volume. Move to PostgreSQL before introducing replicas, background workers that write independently, or an availability target that requires automatic failover.

## Why not D1 in this deployment

Cloudflare D1 uses SQLite semantics but is accessed primarily through a Worker binding. Its built-in REST API is a control-plane interface and Cloudflare recommends a proxy Worker for applications outside Workers. Replacing `Store` with a D1 HTTP client would introduce another service, remote-query latency, different transactions and new failure modes; it is not a drop-in volume replacement for FastAPI.

D1 becomes reasonable if the API itself moves to Workers and tenancy is designed around one or more D1 bindings. That should be a separate implementation sharing telemetry schemas, not conditional branches inside the Python storage class.

References:

- [Cloudflare D1 overview](https://developers.cloudflare.com/d1/)
- [Access D1 outside Workers](https://developers.cloudflare.com/d1/tutorials/build-an-api-to-access-d1/)
- [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/)
- [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/)
- [Tunnel run parameters](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/)

## Operations

The application container runs as UID/GID `10001`. Create the bind-mounted
`backups/` directory with that ownership and mode `0700`; the named `/data`
volume is initialized with the correct ownership by the image.

The frontend build uses the official npm registry by default. On a network
where that endpoint is unavailable, an operator can select a trusted compatible
registry without editing the Dockerfile; package tarballs are still verified
against the integrity values in `package-lock.json`:

```bash
docker compose build \
  --build-arg NPM_CONFIG_REGISTRY=https://your-trusted-registry.example \
  vrchat-monitor
```

1. Check `docker compose ps` and `/readyz` after every deployment.
2. Run a backup before upgrades and require a recent off-site R2 upload.
3. Require the automated daily downloaded restore drill to be recent; run another
   explicit drill before a release or storage migration.
4. Review container logs for repeated 401, 429 and 5xx responses; do not log credential values or payload bodies.
5. Rotate the bootstrap token after provisioning. Rotate collector/access/tunnel credentials after disclosure or when a user leaves.
6. Apply host security updates during a planned restart window.

The default local-backup RPO is one hour with 48 local artifacts. The optional R2
profile stores immutable hourly, daily and monthly copies with lifecycle retention of
8, 93 and 400 days. Alert when `.last-local-success.json` or
`.last-offsite-success.json` is older than two hours, or
`.last-restore-drill.json` is older than twenty-six hours. A restore drill verifies
compressed and uncompressed SHA-256 values, the hosted schema, table counts and
`PRAGMA integrity_check` before any production replacement is considered.

The snapshot service mounts `/data` read-write because a correct read-only
connection to a live WAL database may need SQLite shared-memory bookkeeping. Its
connection is still `mode=ro` plus `query_only`. The off-site service mounts only the
backup directory and its Docker secret; it cannot access the primary database.

The release workflow is manual-only. With `publish=false` it runs tests, creates a checksummed source archive and builds a container without publishing either. Publishing requires both the boolean input and an exact `RELEASE <version>` confirmation.
