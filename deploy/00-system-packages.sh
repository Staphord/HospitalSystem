#!/usr/bin/env bash
# Installs every OS-level dependency needed to run the stack without Docker:
# Python 3.11, PostgreSQL, Redis, RabbitMQ, Nginx, Node.js (to build the frontend),
# a JRE (for Keycloak), and basic build tools for native Python wheels.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  software-properties-common curl wget unzip git ufw \
  build-essential libpq-dev libssl-dev \
  postgresql postgresql-contrib \
  redis-server \
  rabbitmq-server \
  nginx \
  openjdk-17-jre-headless

# Ubuntu 22.04/24.04 ship Python 3.10/3.12 by default; the services target 3.11.
if ! command -v python3.11 >/dev/null 2>&1; then
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update
  apt-get install -y python3.11 python3.11-venv python3.11-dev
fi

# Node.js 20.x, only needed to build the React frontend on the box.
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

echo "System packages installed."
echo "Python: $(python3.11 --version)"
echo "Node:   $(node --version)"
echo "Postgres: $(pg_lsclusters | tail -n1 || true)"
