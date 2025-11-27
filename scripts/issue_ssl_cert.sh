#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: scripts/issue_ssl_cert.sh <domain> [--wildcard]

Issue an EC-256 certificate via acme.sh + DNSPod DNS-01.
Environment:
  DP_Id / DP_Key       Required. DNSPod API ID and Token.
  ACME_EMAIL           Optional. Account email for Let's Encrypt.
  ACME_RELOAD_CMD      Optional. Run after install/renew (e.g. "systemctl reload nginx").
  ACME_SERVER          Optional. Default "letsencrypt". You may use "zerossl" etc.

Examples:
  DP_Id=xxx DP_Key=yyy scripts/issue_ssl_cert.sh proxy.example.com
  DP_Id=xxx DP_Key=yyy scripts/issue_ssl_cert.sh example.com --wildcard
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

DOMAIN="$1"
WILDCARD=0
if [[ "${2:-}" == "--wildcard" ]]; then
  WILDCARD=1
fi

: "${DP_Id:?DP_Id is required (DNSPod API ID)}"
: "${DP_Key:?DP_Key is required (DNSPod API Token)}"

ACME_SERVER="${ACME_SERVER:-letsencrypt}"
ACME_EMAIL="${ACME_EMAIL:-}"
ACME_SH="${ACME_SH:-$HOME/.acme.sh/acme.sh}"
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
