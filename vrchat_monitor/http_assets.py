from __future__ import annotations

import urllib.parse
from pathlib import Path


def static_asset_for_request(root: Path, request_target: str) -> tuple[Path, str] | None:
    """Map a request to one of the three immutable local UI assets.

    The request never becomes part of a filesystem path. Keeping this allowlist
    explicit also prevents newly added files from becoming public by accident.
    """

    route = urllib.parse.urlsplit(request_target).path
    if route in {"/", "/index.html"}:
        return root / "index.html", "text/html; charset=utf-8"
    if route == "/styles.css":
        return root / "styles.css", "text/css; charset=utf-8"
    if route == "/app.js":
        return root / "app.js", "text/javascript; charset=utf-8"
    return None
