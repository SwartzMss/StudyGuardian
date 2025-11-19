#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run this script as root or via sudo so it can install PostgreSQL and configure the service."
  exit 1
fi

cd /tmp

DB_NAME="${PGSETUP_DB_NAME:-study_guardian}"
DB_USER="${PGSETUP_DB_USER:-guardian}"
DB_PASS="${PGSETUP_DB_PASS:-study_guardian}"

echo "Installing PostgreSQL on Raspberry Pi 5 (Debian/Ubuntu)..."
apt update
apt install -y postgresql postgresql-contrib

echo "Enabling PostgreSQL service..."
systemctl enable --now postgresql

echo "Configuring PostgreSQL to listen on all interfaces..."
PG_CONF_PATH="$(sudo -u postgres psql -tAc 'SHOW config_file;' | tr -d '[:space:]')"
PG_HBA_PATH="$(dirname "$PG_CONF_PATH")/pg_hba.conf"
if ! grep -Eq "^listen_addresses" "$PG_CONF_PATH"; then
  echo "listen_addresses = '0.0.0.0'" >> "$PG_CONF_PATH"
else
  perl -0pi -e "s/^\\s*#?listen_addresses\\s*=\\s*'[^']*'/listen_addresses = '0.0.0.0'/m" "$PG_CONF_PATH"
fi

if ! grep -Eq "^host\\s+all\\s+all\\s+0\\.0\\.0\\.0/0" "$PG_HBA_PATH"; then
  cat >> "$PG_HBA_PATH" <<'EOF'
# Allow remote admin/query clients; restrict via firewall if needed.
host all all 0.0.0.0/0 md5
EOF
fi

echo "Restarting PostgreSQL so network changes take effect..."
systemctl restart postgresql

echo "Ensuring role '$DB_USER' exists with the provided password..."
sudo -u postgres psql -v ON_ERROR_STOP=1 <<-SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$DB_USER') THEN
    CREATE ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASS';
  ELSE
    ALTER ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASS';
  END IF;
END
\$\$;
SQL

echo "Ensuring database '$DB_NAME' exists and is owned by '$DB_USER'..."
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1; then
  sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
else
  sudo -u postgres psql -c "ALTER DATABASE \"$DB_NAME\" OWNER TO \"$DB_USER\";"
fi

echo
echo "=========================================="
echo "PostgreSQL setup completed."
echo "Use the following DSN in config/settings.yaml -> storage.postgres_dsn:"
echo "postgresql://$DB_USER:$DB_PASS@localhost/$DB_NAME"
echo
echo "If this DSN is too permissive, consider restricting it by creating a tighter user/password."
echo "———————————————————————————————————————————"
