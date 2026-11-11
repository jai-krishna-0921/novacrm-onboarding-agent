#!/bin/bash
# EC2 first boot (Amazon Linux 2023): Docker + Compose plugin + 2 GB swap.
set -eux
dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap defaults 0 0' >> /etc/fstab
mkdir -p /opt/novacrm && chown ec2-user:ec2-user /opt/novacrm
touch /var/lib/cloud/novacrm-ready
