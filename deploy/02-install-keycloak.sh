#!/usr/bin/env bash
# Installs Keycloak as a systemd service, bound to 127.0.0.1 only.
# Nothing in this app sends a browser to Keycloak's own login UI — auth-service
# talks to it server-side (direct grant + admin API) — so it never needs to be
# reachable from the internet.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

KEYCLOAK_VERSION="24.0.5"
KC_HOME="/opt/keycloak"
KC_ADMIN_USER="${KC_ADMIN_USER:-admin}"
KC_ADMIN_PASSWORD="${KC_ADMIN_PASSWORD:-$(openssl rand -hex 16)}"

# Resolve the repo root (parent of this deploy/ folder) so we can pick up the
# checked-in realm export regardless of where this script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REALM_EXPORT="${REPO_DIR}/keycloak/realm-export.json"

if [ ! -f "${REALM_EXPORT}" ]; then
  echo "Could not find ${REALM_EXPORT} — run this from a checkout of the HospitalSystem repo." >&2
  exit 1
fi

id -u keycloak &>/dev/null || useradd -r -s /usr/sbin/nologin -d "${KC_HOME}" keycloak

if [ ! -d "${KC_HOME}" ]; then
  cd /tmp
  wget -q "https://github.com/keycloak/keycloak/releases/download/${KEYCLOAK_VERSION}/keycloak-${KEYCLOAK_VERSION}.tar.gz"
  tar -xzf "keycloak-${KEYCLOAK_VERSION}.tar.gz"
  mv "keycloak-${KEYCLOAK_VERSION}" "${KC_HOME}"
  rm -f "keycloak-${KEYCLOAK_VERSION}.tar.gz"
fi

mkdir -p "${KC_HOME}/data/import"
cp "${REALM_EXPORT}" "${KC_HOME}/data/import/realm-export.json"
chown -R keycloak:keycloak "${KC_HOME}"

cat >/etc/systemd/system/keycloak.service <<EOF
[Unit]
Description=Keycloak (identity provider for Hospital Flow)
After=network.target postgresql.service

[Service]
Type=simple
User=keycloak
Group=keycloak
Environment=KC_BOOTSTRAP_ADMIN_USERNAME=${KC_ADMIN_USER}
Environment=KC_BOOTSTRAP_ADMIN_PASSWORD=${KC_ADMIN_PASSWORD}
WorkingDirectory=${KC_HOME}
ExecStart=${KC_HOME}/bin/kc.sh start-dev --http-host=127.0.0.1 --http-port=8080 --import-realm --hostname-strict=false
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable keycloak
systemctl restart keycloak

echo
echo "=============================================================="
echo "Keycloak installed at ${KC_HOME}, bound to 127.0.0.1:8080."
echo "Bootstrap admin: ${KC_ADMIN_USER} / ${KC_ADMIN_PASSWORD}"
echo "Save this password — you'll need it for KEYCLOAK_ADMIN_PASSWORD"
echo "in deploy/env.template."
echo
echo "Note: --import-realm only imports on first boot into an empty DB."
echo "Tail 'journalctl -u keycloak -f' and wait for 'Keycloak ... started'"
echo "before continuing to the next step."
echo "=============================================================="
