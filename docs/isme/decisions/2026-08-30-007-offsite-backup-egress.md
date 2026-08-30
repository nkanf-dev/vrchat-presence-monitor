# SKMB-2026-08-30-007: Off-site Backup Egress

- status: proposed
- decided_by: statistical_default
- approval_source: user authorized unresolved best-practice progress with “通过，后续无需再审批”
- date: 2026-08-30
- commit: 97417e9
- patterns:
  - D_external_dependency
  - E_security_boundary
  - F_fail_semantics
- scope: off-site backup gateway network routing

## Context

The production DNS path returns a Mihomo fake-IP address for the Cloudflare backup
gateway. The backup client deliberately ignores ambient proxy variables, so a direct
TLS connection targets the synthetic address and times out. The existing fixed-node
deployment proxy reaches the same authenticated HTTPS gateway successfully.

## Decision

Off-site backup and restore commands may opt into one explicitly configured HTTP or
HTTPS proxy URL. Ambient proxy variables remain ignored, proxy URLs containing
credentials or paths are rejected, and the backup gateway itself still requires
HTTPS with normal certificate verification. If the proxy is unavailable, off-site
replication fails closed while verified local snapshots remain intact; it does not
silently retry through a different egress path.

## Applies To

- `scripts/r2_backup.py`
- `scripts/restore_hosted.py`
- `docker-compose.yml`
- production `BACKUP_PROXY_URL`
- off-site upload and restore-drill health checks

## Review Debt

Confirm whether the deployment proxy should remain the long-term backup egress or be
replaced by a dedicated direct DNS/egress path.

## Supersedes

None.
