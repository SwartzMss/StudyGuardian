#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: scripts/issue_ssl_cert.sh [<domain>] [--wildcard]

Issue an EC-256 certificate via acme.sh + DNSPod DNS-01.
Defaults are read from config/settings.yaml > ssl.* unless overridden by CLI/env.
Environment:
  DP_Id / DP_Key       Required. DNSPod API ID and Token.
  ACME_EMAIL           Optional. Account email for Let's Encrypt.
  ACME_RELOAD_CMD      Optional. Run after install/renew (e.g. "systemctl reload nginx").
  ACME_SERVER          Optional. Default "letsencrypt". You may use "zerossl" etc.
  SETTINGS_PATH        Optional. Override config path (default: config/settings.yaml).

Examples:
  DP_Id=xxx DP_Key=yyy scripts/issue_ssl_cert.sh proxy.example.com
  DP_Id=xxx DP_Key=yyy scripts/issue_ssl_cert.sh example.com --wildcard
EOF
}

SETTINGS_PATH="${SETTINGS_PATH:-$ROOT_DIR/config/settings.yaml}"

CFG_SSL_DOMAIN=""
CFG_SSL_WILDCARD=0
CFG_SSL_DP_ID=""
CFG_SSL_DP_KEY=""
CFG_SSL_ACME_EMAIL=""
CFG_SSL_ACME_SERVER=""
CFG_SSL_RELOAD_CMD=""

load_ssl_config() {
  local output
  if ! output="$(SETTINGS_PATH="$SETTINGS_PATH" python <<'PY'
import os
import shlex
import sys

try:
    import yaml
except Exception as exc:
    print(f"warning: failed to import PyYAML ({exc}); skipping config defaults", file=sys.stderr)
    sys.exit(0)

settings_path = os.environ["SETTINGS_PATH"]
if not os.path.exists(settings_path):
    sys.exit(0)

with open(settings_path, "r", encoding="utf-8") as f:
    data = yaml.safe_load(f) or {}

ssl_cfg = data.get("ssl") or {}

def emit(key: str, value):
    if value in (None, ""):
        return
    print(f"{key}={shlex.quote(str(value))}")

emit("CFG_SSL_DOMAIN", ssl_cfg.get("domain"))
emit("CFG_SSL_WILDCARD", int(bool(ssl_cfg.get("wildcard"))))
emit("CFG_SSL_DP_ID", ssl_cfg.get("dp_id"))
emit("CFG_SSL_DP_KEY", ssl_cfg.get("dp_key"))
emit("CFG_SSL_ACME_EMAIL", ssl_cfg.get("acme_email"))
emit("CFG_SSL_ACME_SERVER", ssl_cfg.get("acme_server"))
emit("CFG_SSL_RELOAD_CMD", ssl_cfg.get("reload_cmd"))
PY
  )"; then
    echo "warning: failed to read $SETTINGS_PATH; continuing without config defaults" >&2
    return
  fi

  if [[ -n "$output" ]]; then
    eval "$output"
  fi
}

DOMAIN=""
WILDCARD=0

for arg in "$@"; do
  case "$arg" in
    --wildcard)
      WILDCARD=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "$DOMAIN" ]]; then
        DOMAIN="$arg"
      else
        echo "error: unexpected argument: $arg" >&2
        usage
        exit 1
      fi
      ;;
  esac
done

load_ssl_config

if [[ -z "$DOMAIN" ]]; then
  DOMAIN="$CFG_SSL_DOMAIN"
fi

if [[ -z "$DOMAIN" ]]; then
  usage
  exit 1
fi

if [[ "$WILDCARD" -eq 0 ]]; then
  cfg_wildcard="${CFG_SSL_WILDCARD:-0}"
  if [[ "$cfg_wildcard" == "1" || "$cfg_wildcard" == "true" ]]; then
    WILDCARD=1
  fi
fi

if [[ -z "${DP_Id:-}" ]]; then
  DP_Id="$CFG_SSL_DP_ID"
fi
if [[ -z "${DP_Key:-}" ]]; then
  DP_Key="$CFG_SSL_DP_KEY"
fi

: "${DP_Id:?DP_Id is required (DNSPod API ID)}"
: "${DP_Key:?DP_Key is required (DNSPod API Token)}"

ACME_SERVER="${ACME_SERVER:-${CFG_SSL_ACME_SERVER:-letsencrypt}}"
ACME_EMAIL="${ACME_EMAIL:-${CFG_SSL_ACME_EMAIL:-}}"
ACME_SH="${ACME_SH:-$HOME/.acme.sh/acme.sh}"
ACME_RELOAD_CMD="${ACME_RELOAD_CMD:-${CFG_SSL_RELOAD_CMD:-}}"
KEYLENGTH="ec-256"

if [[ ! -x "$ACME_SH" ]]; then
  if ! command -v curl >/dev/null 2>&1; then
    echo "error: curl is required to install acme.sh automatically" >&2
    exit 1
  fi
  echo "acme.sh not found at $ACME_SH, installing..."
  curl https://get.acme.sh | sh
fi

if [[ ! -x "$ACME_SH" ]]; then
  echo "error: acme.sh still not found after install, aborting" >&2
  exit 1
fi

ISSUE_DOMAINS=("-d" "$DOMAIN")
if [[ "$WILDCARD" -eq 1 ]]; then
  ISSUE_DOMAINS=("-d" "*.$DOMAIN" "-d" "$DOMAIN")
fi

echo "Using acme.sh: $ACME_SH"
echo "Default CA: $ACME_SERVER"
if [[ -n "$ACME_EMAIL" ]]; then
  echo "Account email: $ACME_EMAIL"
fi

"$ACME_SH" --set-default-ca --server "$ACME_SERVER"
if [[ -n "$ACME_EMAIL" ]]; then
  "$ACME_SH" --register-account -m "$ACME_EMAIL" --server "$ACME_SERVER" || true
else
  "$ACME_SH" --register-account --server "$ACME_SERVER" || true
fi

echo "Issuing certificate for: ${ISSUE_DOMAINS[*]}"
"$ACME_SH" --issue --dns dns_dp "${ISSUE_DOMAINS[@]}" --keylength "$KEYLENGTH"

CERT_DIR="$HOME/.acme.sh/${DOMAIN}_ecc"
KEY_PATH="$CERT_DIR/${DOMAIN}.key"
CHAIN_PATH="$CERT_DIR/fullchain.cer"

INSTALL_ARGS=(
  --install-cert -d "$DOMAIN"
  --key-file "$KEY_PATH"
  --fullchain-file "$CHAIN_PATH"
)

if [[ "$WILDCARD" -eq 1 ]]; then
  INSTALL_ARGS=(--install-cert -d "*.$DOMAIN" -d "$DOMAIN" --key-file "$KEY_PATH" --fullchain-file "$CHAIN_PATH")
fi

if [[ -n "${ACME_RELOAD_CMD:-}" ]]; then
  INSTALL_ARGS+=(--reloadcmd "$ACME_RELOAD_CMD")
fi

echo "Installing certificate to:"
echo "  Key:       $KEY_PATH"
echo "  Fullchain: $CHAIN_PATH"

"$ACME_SH" "${INSTALL_ARGS[@]}"

cat <<EOF

Done.
Cert files:
  $CHAIN_PATH
  $KEY_PATH

Use them in Nginx:
  ssl_certificate     $CHAIN_PATH;
  ssl_certificate_key $KEY_PATH;
EOF
