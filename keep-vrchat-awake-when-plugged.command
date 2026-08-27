#!/bin/zsh
set -euo pipefail

echo "VRChat 监控电源策略"
echo "插电：电脑不自动睡眠，显示器仍按系统设置熄灭"
echo "电池：允许正常睡眠"
echo

if [[ "$(id -u)" -ne 0 ]]; then
  echo "需要输入 macOS 管理员密码来修改 pmset 电源策略。"
  sudo -v
fi

# Only change the AC sleep timer. Battery sleep, hibernatemode, sleepimage
# and other sleep mechanics are intentionally left untouched.
sudo pmset -c sleep 0

echo
echo "已应用。当前配置："
pmset -g custom | sed -n '/Battery Power:/,/AC Power:/p; /AC Power:/,$p' | grep -E '^(Battery Power:|AC Power:|[[:space:]]+sleep[[:space:]])' || true
echo
echo "完成。关闭此窗口即可。"
