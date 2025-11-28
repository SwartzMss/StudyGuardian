#!/usr/bin/env bash
set -euo pipefail

# Deploy helper to build/install systemd services, sync static assets, and configure nginx.
# Services (with nginx fronting the frontend) start in order: agent -> backend.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETTINGS_PATH="${SETTINGS_PATH:-$ROOT/config/settings.yaml}"
FRONTEND_BUILD="$ROOT/frontend/dist"
STATIC_DEST="${STATIC_DEST:-/var/www/studyguardian}"
ORIG_USER="${SUDO_USER:-$(id -un)}"
ORIG_HOME="$(getent passwd "$ORIG_USER" | cut -d: -f6)"
ensure_root() {
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "This script must be run as root (use sudo)." >&2
    exit 1
  fi
}

ensure_root

SERVICE_USER="${SERVICE_USER:-root}"
SERVICE_GROUP="${SERVICE_GROUP:-$SERVICE_USER}"
SERVICES=("studyguardian-agent" "studyguardian-backend")

AGENT_UNIT_PATH="/etc/systemd/system/studyguardian-agent.service"
BACKEND_UNIT_PATH="/etc/systemd/system/studyguardian-backend.service"

usage() {
  echo "Usage: $0 [install|start|stop|restart|status|build|clean-static]" >&2
  exit 1
}

ACTION="${1:-start}"
shift || true
NGINX_SERVICE="${NGINX_SERVICE:-nginx}"

build() {
  if [[ -n "$ORIG_HOME" ]]; then
    export HOME="$ORIG_HOME"
    # Ensure user-level toolchains (e.g., cargo via rustup) are on PATH.
    if [[ -f "$ORIG_HOME/.cargo/env" ]]; then
      # shellcheck disable=SC1090
      source "$ORIG_HOME/.cargo/env"
    fi
    # Ensure nvm-managed node/npm are available if present.
    if [[ -s "$ORIG_HOME/.nvm/nvm.sh" ]]; then
      export NVM_DIR="${NVM_DIR:-$ORIG_HOME/.nvm}"
      # shellcheck disable=SC1090
      source "$ORIG_HOME/.nvm/nvm.sh"
    fi
  fi
  bash "$ROOT/scripts/build.sh"
}

sync_static_assets() {
  STATIC_ROOT="$STATIC_DEST"
  if [[ ! -d "$FRONTEND_BUILD" ]]; then
    echo "frontend build not found at $FRONTEND_BUILD; run build first" >&2
    exit 1
  fi
  mkdir -p "$STATIC_ROOT"
  rsync -a --delete "$FRONTEND_BUILD"/ "$STATIC_ROOT"/
}

clean_static() {
  [[ -d "$STATIC_DEST" ]] || { echo "No static dir at $STATIC_DEST"; return; }
  rm -rf "$STATIC_DEST"
  echo "Removed static assets at $STATIC_DEST"
}

read_nginx_vars() {
  eval "$(
    python - <<'PY' "$SETTINGS_PATH" "$ROOT"
import sys
from pathlib import Path

settings_path = Path(sys.argv[1])
root = Path(sys.argv[2])
if not settings_path.exists():
    print(f'echo "settings file not found: {settings_path}" >&2; exit 1')
    sys.exit(0)

try:
    import yaml
except ImportError:
    print('echo "PyYAML not installed; cannot parse settings.yaml" >&2; exit 1')
    sys.exit(0)

data = yaml.safe_load(settings_path.read_text()) or {}
server = data.get("server") or {}
ssl = data.get("ssl") or {}

domain = ssl.get("domain") or ""
external_port = server.get("external_port") or 443
backend_bind = server.get("backend_bind") or "127.0.0.1:8000"
cert_path = ssl.get("cert_path") or ""
key_path = ssl.get("key_path") or ""

print(f'DOMAIN="{domain}"')
print(f'EXTERNAL_PORT="{external_port}"')
print(f'BACKEND_BIND="{backend_bind}"')
print(f'CERT_PATH="{cert_path}"')
print(f'KEY_PATH="{key_path}"')
PY
  )"

  if [[ -z "${DOMAIN:-}" || -z "${CERT_PATH:-}" || -z "${KEY_PATH:-}" ]]; then
    echo "nginx config requires domain, cert_path, key_path in $SETTINGS_PATH" >&2
    exit 1
  fi
}

configure_nginx() {
  read_nginx_vars
  sync_static_assets

  local nginx_conf="/etc/nginx/sites-available/studyguardian.conf"
  sudo tee "$nginx_conf" >/dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    return 301 https://\$host:$EXTERNAL_PORT\$request_uri;
}

server {
    listen $EXTERNAL_PORT ssl;
    server_name $DOMAIN;

    ssl_certificate $CERT_PATH;
    ssl_certificate_key $KEY_PATH;

    root $STATIC_ROOT;
    index index.html;

    location /api/ {
        proxy_pass http://$BACKEND_BIND;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;
    }

    location /ws/ {
        proxy_pass http://$BACKEND_BIND;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location / {
        try_files \$uri /index.html;
    }
}
EOF

  sudo ln -sf "$nginx_conf" /etc/nginx/sites-enabled/studyguardian.conf
}

write_unit_files() {
  sudo tee "$AGENT_UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=StudyGuardian Agent
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
ExecStart=$ROOT/.venv/bin/python -m agent.main
Restart=on-failure
RestartSec=3
User=$SERVICE_USER
Group=$SERVICE_GROUP

[Install]
WantedBy=multi-user.target
EOF

  sudo tee "$BACKEND_UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=StudyGuardian Backend
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$ROOT/backend
ExecStart=$ROOT/backend/target/release/studyguardian-backend
Restart=on-failure
RestartSec=3
User=$SERVICE_USER
Group=$SERVICE_GROUP

[Install]
WantedBy=multi-user.target
EOF
}

start_services() {
  sudo systemctl daemon-reload
  for svc in "${SERVICES[@]}"; do
    sudo systemctl start "${svc}.service"
  done
}

stop_services() {
  for ((idx=${#SERVICES[@]}-1; idx>=0; idx--)); do
    sudo systemctl stop "${SERVICES[idx]}.service" >/dev/null 2>&1 || true
  done
}

status_services() {
  for svc in "${SERVICES[@]}"; do
    sudo systemctl status "${svc}.service" --no-pager
  done
}

reload_nginx() {
  sudo nginx -t
  sudo systemctl reload "${NGINX_SERVICE}.service"
}

case "$ACTION" in
  install)
    stop_services
    build
    write_unit_files
    configure_nginx
    start_services
    reload_nginx
    ;;
  build)
    build
    ;;
  start)
    build
    write_unit_files
    configure_nginx
    start_services
    reload_nginx
    ;;
  stop)
    stop_services
    ;;
  restart)
    stop_services
    build
    write_unit_files
    configure_nginx
    start_services
    reload_nginx
    ;;
  status)
    status_services
    ;;
  clean-static)
    clean_static
    ;;
  *)
    usage
    ;;
esac
