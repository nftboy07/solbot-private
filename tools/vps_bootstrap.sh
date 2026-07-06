#!/usr/bin/env bash
set -euo pipefail

cd /root/solbot-production

# Swap for low-memory VPS
if ! swapon --show | grep -q '/swapfile'; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

python3 tools/ensure_rpc_fallback.py
python3 tools/cleanup_state.py
cp -f ops/logrotate.solbot /etc/logrotate.d/solbot
mkdir -p /etc/systemd/system/solbot.service.d
cp -f ops/solbot.service.d/limits.conf /etc/systemd/system/solbot.service.d/limits.conf
systemctl daemon-reload
systemctl restart solbot.service
systemctl is-active solbot.service