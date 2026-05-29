#!/bin/bash
set -euo pipefail
DATA_DEV=/dev/disk/by-id/google-algo-data
DATA_MOUNT=/mnt/disks/algo-data

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq xfsprogs git chrony ca-certificates curl gnupg

if [[ -b "$DATA_DEV" ]]; then
  mkdir -p "$DATA_MOUNT"
  if ! blkid "$DATA_DEV" >/dev/null 2>&1; then
    mkfs.xfs -f "$DATA_DEV"
  fi
  grep -q "$DATA_MOUNT" /etc/fstab || echo "$DATA_DEV $DATA_MOUNT xfs defaults 0 2" >> /etc/fstab
  mount -a
  mkdir -p "$DATA_MOUNT/data"
  mkdir -p /var/lib/algo-trading
  ln -sfn "$DATA_MOUNT/data" /var/lib/algo-trading/data
fi

if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

systemctl enable --now docker chrony
