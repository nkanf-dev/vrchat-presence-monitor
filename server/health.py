from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .storage import Store


def _age(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(
        0,
        int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()),
    )


def _category(account_state: str, latest_error: str) -> str | None:
    if account_state == "reconnect" or latest_error == "session_expired":
        return "session_expired"
    if latest_error == "network":
        return "site_network"
    if latest_error in {"rate_limited", "upstream", "not_found"}:
        return "vrchat_service"
    if latest_error:
        return "collector_failure"
    return None


class TenantHealthService:
    def __init__(self, store: Store):
        self.store = store

    def list(self) -> dict[str, Any]:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(
            timespec="microseconds"
        )
        with self.store.lock, self.store.connection() as db:
            tenants = db.execute(
                """SELECT t.id,t.name,a.state AS account_state,a.last_sync AS account_sync,
                c.last_sync AS collector_sync
                FROM tenants t
                LEFT JOIN vrchat_accounts a ON a.tenant_id=t.id
                LEFT JOIN collectors c ON c.id=a.collector_id
                ORDER BY t.created_at,t.id"""
            ).fetchall()
            items: list[dict[str, Any]] = []
            for row in tenants:
                counts = db.execute(
                    """SELECT
                    SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) AS successes,
                    SUM(CASE WHEN outcome='failure' THEN 1 ELSE 0 END) AS failures
                    FROM collection_samples WHERE tenant_id=? AND observed_at>=?""",
                    (row["id"], since),
                ).fetchone()
                latest_sample = db.execute(
                    """SELECT outcome,error_category,observed_at
                    FROM collection_samples WHERE tenant_id=?
                    ORDER BY observed_at DESC,sample_id DESC LIMIT 1""",
                    (row["id"],),
                ).fetchone()
                latest_success = db.execute(
                    """SELECT observed_at FROM collection_samples
                    WHERE tenant_id=? AND outcome='success'
                    ORDER BY observed_at DESC,sample_id DESC LIMIT 1""",
                    (row["id"],),
                ).fetchone()
                account_state = str(row["account_state"] or "not_connected")
                latest_error = (
                    str(latest_sample["error_category"] or "")
                    if latest_sample and latest_sample["outcome"] == "failure"
                    else ""
                )
                last_sync = (
                    str(latest_success["observed_at"])
                    if latest_success
                    else str(row["account_sync"] or row["collector_sync"] or "")
                )
                sync_age = _age(last_sync)
                collector_state = (
                    "never"
                    if sync_age is None
                    else "fresh"
                    if sync_age <= 600
                    else "stale"
                )
                category = _category(account_state, latest_error)
                successes = int(counts["successes"] or 0)
                failures = int(counts["failures"] or 0)
                total = successes + failures
                state = (
                    "needs_login"
                    if account_state == "reconnect"
                    else "degraded"
                    if category is not None
                    else "healthy"
                    if collector_state == "fresh"
                    else "idle"
                    if account_state == "not_connected"
                    else "stale"
                )
                items.append(
                    {
                        "tenant": str(row["id"])[-8:],
                        "name": str(row["name"]),
                        "account_state": account_state,
                        "collector_state": collector_state,
                        "last_success_age_seconds": sync_age,
                        "state": state,
                        "category": category,
                        "recent_successes": successes,
                        "recent_failures": failures,
                        "success_rate": round(successes / total, 4) if total else None,
                    }
                )
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "tenants": items}
