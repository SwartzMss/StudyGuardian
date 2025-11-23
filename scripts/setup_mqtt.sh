#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run this script as root or via sudo so it can install and configure Mosquitto."
  exit 1
fi

cd /tmp

MQTT_USER="${MQTT_USER:-}"
MQTT_PASS="${MQTT_PASS:-}"
MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_CONF="/etc/mosquitto/conf.d/studyguardian.conf"
MQTT_PASSFILE="/etc/mosquitto/passwd"
DEFAULT_CONF="/etc/mosquitto/mosquitto.conf"
AUTH_ENABLED=false
if [ -n "${MQTT_USER}" ]; then
  AUTH_ENABLED=true
  if [ -z "${MQTT_PASS}" ]; then
    echo "MQTT_USER provided but MQTT_PASS is empty; set MQTT_PASS when enabling auth."
    exit 1
  fi
fi

if command -v mosquitto >/dev/null 2>&1; then
  echo "Mosquitto already installed; skipping broker package install."
else
  echo "Installing Mosquitto broker..."
  apt update
  apt install -y mosquitto
fi

if command -v mosquitto_pub >/dev/null 2>&1; then
  echo "mosquitto-clients already installed."
else
  echo "Installing mosquitto-clients..."
  apt update
  apt install -y mosquitto-clients
fi

echo "Enabling Mosquitto service..."
systemctl enable --now mosquitto

EXISTING_LISTENER=false
if grep -E "^[[:space:]]*listener" "$DEFAULT_CONF" >/dev/null 2>&1; then
  EXISTING_LISTENER=true
elif ls /etc/mosquitto/conf.d/*.conf >/dev/null 2>&1; then
  if grep -E "^[[:space:]]*listener" /etc/mosquitto/conf.d/*.conf >/dev/null 2>&1; then
    EXISTING_LISTENER=true
  fi
fi

if [ "$EXISTING_LISTENER" = true ]; then
  echo "Detected existing listener definitions; skipping creation of ${MQTT_CONF} to avoid duplicates."
  if [ "$AUTH_ENABLED" = true ]; then
    echo "NOTE: You requested auth, but an existing config already defines listeners."
    echo "      Please update Mosquitto config manually to enforce auth."
  fi
else
  echo "Configuring Mosquitto listener..."
  if [ -f "$MQTT_CONF" ]; then
    cp "$MQTT_CONF" "${MQTT_CONF}.bak"
    echo "Backed up existing config to ${MQTT_CONF}.bak"
  fi
  {
    echo "# StudyGuardian MQTT broker configuration"
    echo "listener ${MQTT_PORT}"
    if [ "$AUTH_ENABLED" = true ]; then
      echo "allow_anonymous false"
      echo "password_file ${MQTT_PASSFILE}"
    else
      echo "allow_anonymous true"
      echo "# To enable auth, set MQTT_USER and MQTT_PASS env vars when running setup_mqtt.sh"
    fi
    echo "persistence true"
    echo "persistence_location /var/lib/mosquitto/"
  } > "$MQTT_CONF"

  if [ "$AUTH_ENABLED" = true ]; then
    echo "Creating MQTT user '${MQTT_USER}'..."
    mosquitto_passwd -b "$MQTT_PASSFILE" "$MQTT_USER" "$MQTT_PASS"
  fi
fi

echo "Restarting Mosquitto to apply changes..."
systemctl restart mosquitto

if systemctl is-active --quiet mosquitto; then
  echo "Mosquitto service status: active"
else
  echo "Mosquitto service status: inactive (check logs)"
fi

echo
echo "=========================================="
echo "Mosquitto setup completed."
echo "Broker listening on tcp://localhost:${MQTT_PORT}"
if [ "$AUTH_ENABLED" = true ]; then
  echo "Auth enabled"
  echo "Username: ${MQTT_USER}"
  echo "Password: ${MQTT_PASS}"
  echo "DSN: mqtt://${MQTT_USER}:${MQTT_PASS}@localhost:${MQTT_PORT}"
else
  echo "Auth disabled (allow_anonymous true)"
  echo "DSN: mqtt://localhost:${MQTT_PORT}"
fi
echo "=========================================="
