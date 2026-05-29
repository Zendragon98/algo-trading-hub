#!/bin/bash
set -euo pipefail
DATA_DEV=/dev/disk/by-id/google-algo-data
DATA_MOUNT="${data_mount}"
INSTALL_DIR="${install_dir}"

apt-get update -qq
apt-get install -y -qq xfsprogs docker.io docker-compose-plugin git chrony

if [[ -b "$DATA_DEV" ]]; then
  mkdir -p "$DATA_MOUNT"
  if ! blkid "$DATA_DEV" >/dev/null 2>&1; then
    mkfs.xfs -f "$DATA_DEV"
  fi
  grep -q "$DATA_MOUNT" /etc/fstab || echo "$DATA_DEV $DATA_MOUNT xfs defaults 0 2" >> /etc/fstab
  mount -a
  mkdir -p "$DATA_MOUNT/data"
  ln -sfn "$DATA_MOUNT/data" /var/lib/algo-trading/data
fi

systemctl enable --now docker chrony
