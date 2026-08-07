# Hosting Hospital Flow on an Ubuntu VPS (no Docker)

Native install: every backend microservice runs as its own systemd unit inside
its own virtualenv, Postgres/Redis/RabbitMQ/Keycloak run as regular Ubuntu
packages/services, and Nginx is the single public entry point (port 80) that
serves the built React frontend and reverse-proxies `/api/` to the gateway.

Tested against Ubuntu 22.04/24.04. Assumes a bare IP (no domain yet) — add
TLS later with `certbot --nginx` once you point a domain at the box.

## Layout once deployed

```
/opt/hospitalflow/backend/       # this repo, rsynced by 03-setup-app.sh
/opt/hospitalflow/backend/services/<name>/venv/   # one venv per microservice
/opt/hospitalflow/env/common.env                  # shared env, read by every systemd unit
/opt/hospitalflow/frontend-src/                   # HospitalSystem-FrontEnd checkout (build input)
/var/www/hospitalflow/                            # built frontend, served by Nginx
/etc/systemd/system/hospital-*.service            # one per microservice
```

## 0. Get both repos onto the VPS

You need **both** repos on the box: this backend (`HospitalSystem`) and
`HospitalSystem-FrontEnd`. Easiest is pushing each to a Git remote (GitHub/
GitLab/etc.) and cloning on the VPS, e.g.:

```bash
ssh youruser@YOUR_VPS_IP
git clone <your-backend-remote> ~/HospitalSystem
git clone <your-frontend-remote> ~/HospitalSystem-FrontEnd
cd ~/HospitalSystem
```

No remote yet? `rsync -av --exclude node_modules --exclude .git ./HospitalSystem/ youruser@VPS_IP:~/HospitalSystem/` from your machine works just as well.

All commands below assume you're in `~/HospitalSystem` (this repo) on the VPS.

## 1. System packages

```bash
sudo bash deploy/00-system-packages.sh
```

Installs Python 3.11, Postgres, Redis, RabbitMQ, Nginx, Node 20, a JRE (for
Keycloak), and build tools.

## 2. Databases

```bash
sudo bash deploy/01-setup-databases.sh
```

Sets a fresh `postgres` role password, creates the `hospital_master` database,
and locks Redis/RabbitMQ to `127.0.0.1`. **Copy the generated Postgres
password it prints** — you need it in step 4.

## 3. Keycloak

```bash
sudo bash deploy/02-install-keycloak.sh
journalctl -u keycloak -f   # wait for "Keycloak ... started", then Ctrl-C
```

Installs Keycloak bound to `127.0.0.1:8080` and imports the realm checked
into this repo (`keycloak/realm-export.json` — realm `hospital-realm`,
client `hospital-api`, all the app's roles). Keycloak never needs to be
internet-facing: the app talks to it server-side only. **Copy the generated
admin password it prints.**

> Runs in `start-dev` mode for simplicity, matching this repo's own dev
> Docker setup. Fine for getting the app running; if you later need
> Keycloak to survive real production load, switch it to `kc.sh build` +
> `start --optimized` with its own Postgres database instead of embedded
> storage.

## 4. Deploy the app code + build virtualenvs

```bash
sudo bash deploy/03-setup-app.sh
```

Creates the `hospitalflow` system user, rsyncs this repo to
`/opt/hospitalflow/backend`, symlinks `shared/`/`migrations/`/`scripts/`
into the services that need them (same set the Docker Compose file used to
volume-mount), and builds one venv per service (~5-10 min).

## 5. Generate secrets, env file, and systemd units

```bash
sudo PG_PASSWORD='<from step 2>' \
     KEYCLOAK_ADMIN_PASSWORD='<from step 3>' \
     VPS_IP='<your VPS public IP>' \
     bash deploy/04-generate-env-and-systemd.sh
```

Generates a fresh `SECRET_KEY` and `TENANT_DB_ENCRYPTION_KEY` (do **not**
reuse the ones checked into `infrastructure/docker-compose.yml` — those are
public dev secrets), writes `/opt/hospitalflow/env/common.env`, and
generates + starts all 16 `hospital-*.service` units.

Check it worked:

```bash
systemctl status hospital-api-gateway
curl http://127.0.0.1:8000/health
journalctl -u hospital-auth-service -f   # if something's unhealthy
```

## 6. Run master DB migrations + create the super admin

```bash
sudo bash deploy/05-migrate-and-seed.sh
```

Applies Alembic migrations to `hospital_master`, then prompts for a
super-admin username/email/password and creates it in both Keycloak and the
master DB.

You do **not** need to manually provision a hospital/tenant database —
logging into the frontend's Master portal as this super admin and creating a
tenant from the UI drives master-service's own provisioning flow (creates the
tenant's Postgres DB, encrypts its DSN, runs the tenant Alembic migrations,
all automatically via the `tenant.created`/`tenant.provisioned` RabbitMQ
events).

## 7. Build and serve the frontend

```bash
sudo FRONTEND_SRC=~/HospitalSystem-FrontEnd bash deploy/06-deploy-frontend.sh
```

Builds the React app with `VITE_API_BASE_URL=/api/v1` (same-origin — no CORS
needed since Nginx serves both the SPA and the API proxy on port 80),
installs it to `/var/www/hospitalflow`, wires up
`deploy/nginx-hospitalflow.conf`, and opens the firewall to SSH + HTTP only.

Visit `http://YOUR_VPS_IP/` — you should land on the login page. Log in as
the super admin from step 6 at `/master/login` to create your first hospital.

## Day-to-day operations

```bash
# Status / logs for any service
systemctl status hospital-radiology-service
journalctl -u hospital-radiology-service -f

# Restart one service after a code change
sudo rsync -a --exclude venv HospitalSystem/services/radiology-service/ /opt/hospitalflow/backend/services/radiology-service/
sudo systemctl restart hospital-radiology-service

# Restart everything
sudo systemctl restart hospital-*.service

# Redeploy the frontend after a change
sudo FRONTEND_SRC=~/HospitalSystem-FrontEnd bash deploy/06-deploy-frontend.sh
```

## Adding a domain + TLS later

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.example
```

Certbot rewrites `deploy`'s installed Nginx config in place to add the
`listen 443 ssl` block and redirect; no other changes needed since the app
already only knows about relative `/api/v1` paths.

## Production hardening checklist (beyond "it's running")

- [ ] Rotate `SECRET_KEY` / `TENANT_DB_ENCRYPTION_KEY` again if they were ever
      shared/committed anywhere other than this VPS's `common.env`
- [ ] Move Keycloak off `start-dev` to `start --optimized` with its own DB
- [ ] Set up automated Postgres backups (`pg_dump` per tenant DB + master DB;
      admin-service already writes app-level backups to `/var/backups/hospital`)
- [ ] Put a domain + TLS in front (see above)
- [ ] Consider `pgbouncer` if you provision many tenant databases
- [ ] Set `KEYCLOAK_INTROSPECT=true` in `common.env` once request volume can
      absorb the extra round-trip — it lets you instantly revoke a disabled
      user's access instead of waiting for their token to expire
