#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run this script as root or via sudo so it can install and configure Nginx."
  exit 1
fi

cd /tmp

if command -v nginx >/dev/null 2>&1; then
  echo "Nginx already installed; skipping package install."
else
  echo "Installing Nginx..."
  apt update
  apt install -y nginx
fi

echo "Enabling Nginx service at boot and starting it now..."
systemctl enable --now nginx

echo
echo "=========================================="
echo "Nginx setup completed."
echo "Installed package: nginx"
echo "Service: enabled and running via systemd"
echo "Default site remains enabled. Add your own config under /etc/nginx/sites-available/ and reload nginx when ready."
echo "=========================================="
