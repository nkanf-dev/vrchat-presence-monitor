#!/bin/zsh
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.picoworks.vrchat-monitor.plist"
WATCHDOG_PLIST="$HOME/Library/LaunchAgents/com.picoworks.vrchat-tunnel-watchdog.plist"
BRIDGE_PLIST="$HOME/Library/LaunchAgents/com.picoworks.vrchat-presence-bridge.plist"
ENABLE_TUNNEL_WATCHDOG="${VRCHAT_MONITOR_ENABLE_TUNNEL_WATCHDOG:-0}"
PUBLIC_URL="${VRCHAT_MONITOR_PUBLIC_URL:-}"
BRIDGE_REMOTE_URL="${PRESENCE_REMOTE_URL:-}"
BRIDGE_TOKEN_FILE="${PRESENCE_COLLECTOR_TOKEN_FILE:-}"
ENABLE_BRIDGE="0"
PYTHON_BIN=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [[ -x "$candidate" ]]; then
    PYTHON_BIN="$candidate"
    break
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  echo "找不到 python3，请先安装 Python 3。" >&2
  exit 1
fi
mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$HOME/.picoworks-vrchat-monitor"
if [[ "$ENABLE_TUNNEL_WATCHDOG" == "1" && "$PUBLIC_URL" != https://* ]]; then
  echo "启用隧道守护时，VRCHAT_MONITOR_PUBLIC_URL 必须是 HTTPS 地址。" >&2
  exit 1
fi
if [[ -n "$BRIDGE_REMOTE_URL" || -n "$BRIDGE_TOKEN_FILE" ]]; then
  if [[ -z "$BRIDGE_REMOTE_URL" || -z "$BRIDGE_TOKEN_FILE" ]]; then
    echo "启用 Hosted bridge 时必须同时设置 PRESENCE_REMOTE_URL 和 PRESENCE_COLLECTOR_TOKEN_FILE。" >&2
    exit 1
  fi
  ENABLE_BRIDGE="1"
  mkdir -p "$HOME/.presence-monitor"
  chmod 700 "$HOME/.presence-monitor"
fi
export APP_DIR PYTHON_BIN DATA_DIR="$HOME/.picoworks-vrchat-monitor" ENABLE_TUNNEL_WATCHDOG PUBLIC_URL
export ENABLE_BRIDGE BRIDGE_REMOTE_URL BRIDGE_TOKEN_FILE BRIDGE_DIR="$HOME/.presence-monitor"
"$PYTHON_BIN" - <<'PY'
import os
import stat
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

root = Path(os.environ["APP_DIR"])
values = {
    "__APP_DIR__": escape(str(root)),
    "__PYTHON_BIN__": escape(os.environ["PYTHON_BIN"]),
    "__DATA_DIR__": escape(os.environ["DATA_DIR"]),
    "__PUBLIC_URL__": escape(os.environ.get("PUBLIC_URL", "")),
}
targets = [
    (root / "ops/com.picoworks.vrchat-monitor.plist.template", Path.home() / "Library/LaunchAgents/com.picoworks.vrchat-monitor.plist"),
]
if os.environ.get("ENABLE_TUNNEL_WATCHDOG") == "1":
    targets.append(
        (root / "ops/com.picoworks.vrchat-tunnel-watchdog.plist.template", Path.home() / "Library/LaunchAgents/com.picoworks.vrchat-tunnel-watchdog.plist")
    )
if os.environ.get("ENABLE_BRIDGE") == "1":
    remote_url = os.environ["BRIDGE_REMOTE_URL"].rstrip("/")
    parsed = urlsplit(remote_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SystemExit("PRESENCE_REMOTE_URL 必须是有效的 HTTPS 地址")
    token_file = Path(os.environ["BRIDGE_TOKEN_FILE"]).expanduser().resolve()
    if not token_file.is_file():
        raise SystemExit("PRESENCE_COLLECTOR_TOKEN_FILE 不存在")
    if stat.S_IMODE(token_file.stat().st_mode) & 0o077:
        raise SystemExit("collector token 文件权限过宽；请先 chmod 600")
    bridge_dir = Path(os.environ["BRIDGE_DIR"]).resolve()
    values.update(
        {
            "__BRIDGE_REMOTE_URL__": escape(remote_url),
            "__BRIDGE_TOKEN_FILE__": escape(str(token_file)),
            "__BRIDGE_DIR__": escape(str(bridge_dir)),
        }
    )
    targets.append(
        (
            root / "ops/com.picoworks.vrchat-presence-bridge.plist.template",
            Path.home() / "Library/LaunchAgents/com.picoworks.vrchat-presence-bridge.plist",
        )
    )
for source, target in targets:
    rendered = source.read_text(encoding="utf-8")
    for key, value in values.items():
        rendered = rendered.replace(key, value)
    target.write_text(rendered, encoding="utf-8")
    target.chmod(0o600)
PY
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
if [[ "$ENABLE_TUNNEL_WATCHDOG" == "1" ]]; then
  launchctl bootout "gui/$(id -u)" "$WATCHDOG_PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$WATCHDOG_PLIST"
fi
if [[ "$ENABLE_BRIDGE" == "1" ]]; then
  launchctl bootout "gui/$(id -u)" "$BRIDGE_PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$BRIDGE_PLIST"
fi
open http://127.0.0.1:8842/
echo "已安装常驻启动：$PLIST"
if [[ "$ENABLE_TUNNEL_WATCHDOG" == "1" ]]; then
  echo "已安装隧道健康守护：$WATCHDOG_PLIST"
fi
if [[ "$ENABLE_BRIDGE" == "1" ]]; then
  echo "已安装 Hosted bridge：$BRIDGE_PLIST（每分钟增量同步）"
fi
