#!/usr/bin/env bash
# Builds the React frontend and installs it + the Nginx config that serves it
# alongside a reverse proxy to the API gateway. Also locks the firewall down
# to only expose SSH + HTTP.
#
# Usage:
#   sudo FRONTEND_SRC=/opt/hospitalflow/frontend-src ./deploy/06-deploy-frontend.sh
#
# FRONTEND_SRC must already contain a checkout of the HospitalSystem-FrontEnd
# repo (git clone it there, or rsync it up from your machine, before running).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

: "${FRONTEND_SRC:?Set FRONTEND_SRC to the path of the HospitalSystem-FrontEnd checkout on this box}"

WEB_ROOT="/var/www/hospitalflow"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "${FRONTEND_SRC}/package.json" ]; then
  echo "${FRONTEND_SRC} doesn't look like the frontend repo (no package.json)." >&2
  exit 1
fi

echo "== Building frontend =="
cd "${FRONTEND_SRC}"
echo "VITE_API_BASE_URL=/api/v1" > .env.production
npm ci
npm run build

echo "== Installing to ${WEB_ROOT} =="
mkdir -p "${WEB_ROOT}"
rsync -a --delete "${FRONTEND_SRC}/dist/" "${WEB_ROOT}/"
chown -R www-data:www-data "${WEB_ROOT}"

echo "== Configuring Nginx =="
cp "${SCRIPT_DIR}/nginx-hospitalflow.conf" /etc/nginx/sites-available/hospitalflow
ln -sfn /etc/nginx/sites-available/hospitalflow /etc/nginx/sites-enabled/hospitalflow
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "== Firewall =="
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx HTTP' >/dev/null
ufw --force enable >/dev/null
ufw status verbose

echo
echo "Frontend live. Visit: http://$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')/"
