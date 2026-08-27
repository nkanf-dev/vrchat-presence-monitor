from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MAX_IMPORT_BYTES = 32 * 1024 * 1024
MAX_IMPORT_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_IMPORT_EXPANDED_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_SOURCE_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_IMPORT_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_SOURCE_EXPANDED_BYTES = 512 * 1024 * 1024


def _boolean(value: str, default: bool = False) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _secret(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    file_name = os.environ.get(f"{name}_FILE")
    if value is not None and file_name:
        raise ValueError(f"set only one of {name} or {name}_FILE")
    if file_name:
        path = Path(file_name)
        if not path.is_file():
            raise ValueError(f"{name}_FILE does not point to a readable file")
        return path.read_text(encoding="utf-8").strip()
    return value if value is not None else default


def _session_key(value: str) -> bytes:
    text = str(value or "").strip()
    if not text:
        return b""
    try:
        decoded = base64.b64decode(
            text + "=" * (-len(text) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as error:
        raise ValueError("VRCHAT_SESSION_KEY must be URL-safe base64") from error
    if len(decoded) != 32:
        raise ValueError("VRCHAT_SESSION_KEY must decode to 32 bytes")
    return decoded


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    static_dir: Path
    bootstrap_token: str
    cookie_secure: str = "auto"
    trust_proxy_headers: bool = False
    session_days: int = 30
    login_attempts: int = 8
    login_window_seconds: int = 300
    max_import_bytes: int = DEFAULT_MAX_IMPORT_BYTES
    max_import_expanded_bytes: int = DEFAULT_MAX_IMPORT_EXPANDED_BYTES
    max_source_expanded_bytes: int = DEFAULT_MAX_SOURCE_EXPANDED_BYTES
    import_requests: int = 4
    import_window_seconds: int = 600
    max_telemetry_bytes: int = 4 * 1024 * 1024
    collector_requests_per_minute: int = 30
    tenant_friend_limit: int = 10_000
    tenant_event_limit: int = 1_000_000
    minimum_free_bytes: int = 256 * 1024 * 1024
    hosted_vrchat_login: bool = False
    vrchat_session_key: bytes = b""
    hosted_collector_poll_seconds: int = 180
    hosted_collector_concurrency: int = 3
    hosted_collector_max_backoff_seconds: int = 1800

    def __post_init__(self) -> None:
        mode = self.cookie_secure.strip().lower()
        if mode not in {"auto", "always", "never"}:
            raise ValueError("COOKIE_SECURE must be auto, always, or never")
        if not 1 <= self.session_days <= 365:
            raise ValueError("SESSION_DAYS must be between 1 and 365")
        if not 1 <= self.login_attempts <= 100:
            raise ValueError("LOGIN_ATTEMPTS must be between 1 and 100")
        if not 10 <= self.login_window_seconds <= 3600:
            raise ValueError("LOGIN_WINDOW_SECONDS must be between 10 and 3600")
        if not 1024 <= self.max_import_bytes <= MAX_IMPORT_BYTES:
            raise ValueError("MAX_IMPORT_BYTES is outside the supported range")
        if not (
            self.max_import_bytes
            <= self.max_import_expanded_bytes
            <= MAX_IMPORT_EXPANDED_BYTES
        ):
            raise ValueError("MAX_IMPORT_EXPANDED_BYTES is outside the supported range")
        if not (
            self.max_import_expanded_bytes
            <= self.max_source_expanded_bytes
            <= MAX_SOURCE_EXPANDED_BYTES
        ):
            raise ValueError("MAX_SOURCE_EXPANDED_BYTES is outside the supported range")
        if not 1 <= self.import_requests <= 100:
            raise ValueError("IMPORT_REQUESTS must be between 1 and 100")
        if not 10 <= self.import_window_seconds <= 3600:
            raise ValueError("IMPORT_WINDOW_SECONDS must be between 10 and 3600")
        if not 1024 <= self.max_telemetry_bytes <= 64 * 1024 * 1024:
            raise ValueError("MAX_TELEMETRY_BYTES is outside the supported range")
        if not 1 <= self.collector_requests_per_minute <= 600:
            raise ValueError("COLLECTOR_REQUESTS_PER_MINUTE is outside the supported range")
        if self.tenant_friend_limit < 1 or self.tenant_event_limit < 1:
            raise ValueError("tenant quotas must be positive")
        if self.minimum_free_bytes < 0:
            raise ValueError("MINIMUM_FREE_BYTES cannot be negative")
        if self.hosted_vrchat_login and len(self.vrchat_session_key) != 32:
            raise ValueError("hosted VRChat login requires VRCHAT_SESSION_KEY")
        if not 45 <= self.hosted_collector_poll_seconds <= 3600:
            raise ValueError("HOSTED_COLLECTOR_POLL_SECONDS is outside the supported range")
        if not 1 <= self.hosted_collector_concurrency <= 16:
            raise ValueError("HOSTED_COLLECTOR_CONCURRENCY is outside the supported range")
        if not 60 <= self.hosted_collector_max_backoff_seconds <= 86400:
            raise ValueError("HOSTED_COLLECTOR_MAX_BACKOFF_SECONDS is outside the supported range")

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(__file__).resolve().parent
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", "/data")),
            static_dir=Path(os.environ.get("STATIC_DIR", str(root / "static"))),
            bootstrap_token=_secret("BOOTSTRAP_TOKEN"),
            cookie_secure=os.environ.get("COOKIE_SECURE", "auto"),
            trust_proxy_headers=_boolean(os.environ.get("TRUST_PROXY_HEADERS", "0")),
            session_days=int(os.environ.get("SESSION_DAYS", "30")),
            login_attempts=int(os.environ.get("LOGIN_ATTEMPTS", "8")),
            login_window_seconds=int(os.environ.get("LOGIN_WINDOW_SECONDS", "300")),
            max_import_bytes=int(os.environ.get("MAX_IMPORT_BYTES", str(DEFAULT_MAX_IMPORT_BYTES))),
            max_import_expanded_bytes=int(
                os.environ.get(
                    "MAX_IMPORT_EXPANDED_BYTES",
                    str(DEFAULT_MAX_IMPORT_EXPANDED_BYTES),
                )
            ),
            max_source_expanded_bytes=int(
                os.environ.get(
                    "MAX_SOURCE_EXPANDED_BYTES",
                    str(DEFAULT_MAX_SOURCE_EXPANDED_BYTES),
                )
            ),
            import_requests=int(os.environ.get("IMPORT_REQUESTS", "4")),
            import_window_seconds=int(os.environ.get("IMPORT_WINDOW_SECONDS", "600")),
            max_telemetry_bytes=int(os.environ.get("MAX_TELEMETRY_BYTES", str(4 * 1024 * 1024))),
            collector_requests_per_minute=int(os.environ.get("COLLECTOR_REQUESTS_PER_MINUTE", "30")),
            tenant_friend_limit=int(os.environ.get("TENANT_FRIEND_LIMIT", "10000")),
            tenant_event_limit=int(os.environ.get("TENANT_EVENT_LIMIT", "1000000")),
            minimum_free_bytes=int(os.environ.get("MINIMUM_FREE_BYTES", str(256 * 1024 * 1024))),
            hosted_vrchat_login=_boolean(os.environ.get("HOSTED_VRCHAT_LOGIN", "0")),
            vrchat_session_key=_session_key(_secret("VRCHAT_SESSION_KEY")),
            hosted_collector_poll_seconds=int(os.environ.get("HOSTED_COLLECTOR_POLL_SECONDS", "180")),
            hosted_collector_concurrency=int(os.environ.get("HOSTED_COLLECTOR_CONCURRENCY", "3")),
            hosted_collector_max_backoff_seconds=int(
                os.environ.get("HOSTED_COLLECTOR_MAX_BACKOFF_SECONDS", "1800")
            ),
        )
