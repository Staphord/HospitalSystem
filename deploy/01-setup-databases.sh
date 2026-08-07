#!/usr/bin/env bash
# Creates the Postgres role/database used by the master DB and by tenant DB
# provisioning, and locks Redis/RabbitMQ down to localhost-only (everything
# in this stack talks over loopback since there's no Docker network).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

PGPASSWORD_APP="${PGPASSWORD_APP:-$(openssl rand -hex 24)}"

echo "== PostgreSQL =="
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'postgres') THEN
    CREATE ROLE postgres LOGIN SUPERUSER;
  END IF;
END
\$\$;
ALTER USER postgres WITH PASSWORD '${PGPASSWORD_APP}';
SELECT 'CREATE DATABASE hospital_master OWNER postgres'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'hospital_master')\gexec
SQL

# Postgres already binds to 127.0.0.1 by default on Ubuntu's packaged config —
# nothing to change there. Just confirm it's listening.
ss -ltnp 2>/dev/null | grep -q ':5432' && echo "Postgres listening on 5432 (localhost)."

echo "== Redis =="
sed -i 's/^#\?bind .*/bind 127.0.0.1 -::1/' /etc/redis/redis.conf
systemctl restart redis-server
echo "Redis restarted, bound to loopback."

echo "== RabbitMQ =="
cat >/etc/rabbitmq/rabbitmq.conf <<'EOF'
listeners.tcp.default = 127.0.0.1:5672
management.tcp.ip = 127.0.0.1
EOF
systemctl enable rabbitmq-server
systemctl restart rabbitmq-server
echo "RabbitMQ restarted, bound to loopback (default guest/guest user, loopback-only by RabbitMQ policy anyway)."

echo
echo "=============================================================="
echo "Generated Postgres 'postgres' user password:"
echo "  ${PGPASSWORD_APP}"
echo "Save this — you'll paste it into deploy/env.template as PG_PASSWORD"
echo "when running 04-generate-env-and-systemd.sh."
echo "=============================================================="
