#!/bin/zsh
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.picoworks.vrchat-monitor.plist"
WATCHDOG_PLIST="$HOME/Library/LaunchAgents/com.picoworks.vrchat-tunnel-watchdog.plist"
ENABLE_TUNNEL_WATCHDOG="${VRCHAT_MONITOR_ENABLE_TUNNEL_WATCHDOG:-0}"
PUBLIC_URL="${VRCHAT_MONITOR_PUBLIC_URL:-}"
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
export APP_DIR PYTHON_BIN DATA_DIR="$HOME/.picoworks-vrchat-monitor" ENABLE_TUNNEL_WATCHDOG PUBLIC_URL
"$PYTHON_BIN" - <<'PY'
import os
from pathlib import Path
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
for source, target in targets:
    rendered = source.read_text(encoding="utf-8")
    for key, value in values.items():
        rendered = rendered.replace(key, value)
    target.write_text(rendered, encoding="utf-8")
PY
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
if [[ "$ENABLE_TUNNEL_WATCHDOG" == "1" ]]; then
  launchctl bootout "gui/$(id -u)" "$WATCHDOG_PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$WATCHDOG_PLIST"
fi
open http://127.0.0.1:8842/
echo "已安装常驻启动：$PLIST"
if [[ "$ENABLE_TUNNEL_WATCHDOG" == "1" ]]; then
  echo "已安装隧道健康守护：$WATCHDOG_PLIST"
fi
