#!/bin/zsh
set -e
cd "$(dirname "$0")"
export PYTHONPATH="$PWD"
export VRCHAT_MONITOR_PORT="8842"
python3 -m vrchat_monitor.app
