from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


LABEL = "com.picoworks.vrchat-tunnel"
LOCAL_URL = "http://127.0.0.1:8842/"
PUBLIC_URL = os.environ.get("VRCHAT_MONITOR_PUBLIC_URL", "").strip()
DATA_DIR = Path.home() / ".picoworks-vrchat-monitor"
STATE_FILE = DATA_DIR / "tunnel-watchdog.failures"
LOG_FILE = DATA_DIR / "tunnel-watchdog.log"


def probe(url: str, timeout: float) -> int:
    request = Request(url, headers={"User-Agent": "PicoWorksTunnelWatchdog/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except HTTPError as error:
        return int(error.code)
    except (URLError, TimeoutError, OSError):
        return 0


def is_healthy(status_code: int, allow_client_errors: bool = False) -> bool:
    upper = 500 if allow_client_errors else 400
    return 200 <= status_code < upper


def should_restart(consecutive_failures: int) -> bool:
    return consecutive_failures >= 2


def log(message: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")


def read_failures() -> int:
    try:
        return max(0, int(STATE_FILE.read_text(encoding="utf-8").strip()))
    except (FileNotFoundError, ValueError):
        return 0


def write_failures(value: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(f"{value}\n", encoding="utf-8")


def main() -> int:
    if not PUBLIC_URL:
        return 0
    local_code = probe(LOCAL_URL, 8)
    if not is_healthy(local_code):
        write_failures(0)
        return 0

    public_code = probe(PUBLIC_URL, 15)
    if is_healthy(public_code, allow_client_errors=True):
        write_failures(0)
        return 0

    failures = read_failures() + 1
    write_failures(failures)
    log(f"public probe failed (local={local_code}, public={public_code}, consecutive={failures})")
    if not should_restart(failures):
        return 0

    log("restarting tunnel after consecutive public probe failures")
    result = subprocess.run(
        ["/bin/launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        log(f"launchctl kickstart failed ({result.returncode}): {result.stderr.strip()}")
    write_failures(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
