# Hospital Flow Deployment Guide

## Prerequisites

- Docker Desktop (Windows) or Docker Engine (Linux)
- Docker Compose v2+
- PostgreSQL 16+ (or use Docker)
- Redis 7+ (or use Docker)
- RabbitMQ 3.13+ (or use Docker)
- Keycloak 24+ (or use Docker)
- Python 3.11+ (for local development)
- Git

## Environment Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd hospital-flow
```

### 2. Create Environment Files

Create `.env` files from the examples:

```bash
cp services/auth-service/.env.example services/auth-service/.env
cp services/master-service/.env.example services/master-service/.env
# ... for each service
```

### 3. Required Environment Variables

#### Shared Variables
```env
# Database
DATABASE_URL=postgresql://postgres:nasr@localhost:5432/hospital_master
DB_ADMIN_URL=postgresql://postgres:nasr@localhost:5432/postgres
TENANT_DB_TEMPLATE=postgresql://postgres:nasr@localhost:5432/tenant_{tenant_id}

# Redis
REDIS_URL=redis://localhost:6379/0

# Keycloak
KEYCLOAK_URL=http://localhost:8080
KEYCLOAK_REALM=hospital-realm
KEYCLOAK_CLIENT_ID=hospital-api
KEYCLOAK_CLIENT_SECRET=<your-client-secret>
KEYCLOAK_ADMIN_USERNAME=admin
KEYCLOAK_ADMIN_PASSWORD=admin

# Security
SECRET_KEY=<64-char-hex>
TENANT_DB_ENCRYPTION_KEY=<32-byte-base64>

# Application
ENVIRONMENT=dev
ALLOWED_ORIGINS=http://localhost:8000,http://localhost:8501
```

### 4. Keycloak Setup

1. Start Keycloak:
```bash
docker run -p 8080:8080 -e KEYCLOAK_ADMIN=admin -e KEYCLOAK_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:24.0 start-dev
```

2. Create a realm: `hospital-realm`
3. Create a client: `hospital-api` (confidential, OIDC)
4. Set valid redirect URIs: `http://localhost:8000/*`
5. Create realm roles: `super_admin`, `hospital_admin`, `hospital_user`, `nurse`, `clinician`, `doctor`, `patient`
6. Create a test superadmin user: `superadmin` / `superadmin123`
7. Assign `super_admin` role to the superadmin user

## Docker Compose Deployment

### Development Stack

```bash
cd infrastructure
docker compose -p hospital_flow -f docker-compose.yml up -d
```

This starts:
- PostgreSQL Master (port 5432)
- Redis (port 6380)
- RabbitMQ (port 5672 + 15672 for management)
- API Gateway (port 8000)
- Auth Service (port 8001)
- Master Service (port 8002)
- Admin Service (port 8018)
- All clinical services (ports 8010-8017)
- Support services (ports 8019-8020)

### Verify Services

```bash
docker compose -p hospital_flow ps
```

All containers should show `healthy` status.

### View Logs

```bash
docker compose -p hospital_flow logs -f <service-name>
```

### Stop Stack

```bash
docker compose -p hospital_flow down
```

### Full Reset (DANGER: deletes all data)

```bash
docker compose -p hospital_flow down -v
docker volume prune -f
```

## Kubernetes Deployment

### Prerequisites

- Kubernetes cluster (v1.28+)
- kubectl configured
- kubectl configured
- kubectl configured

### Apply Manifests

```bash
cd infrastructure/k8s

# Apply all services
kubectl apply -f api-gateway/
kubectl apply -f auth-service/
kubectl apply -f master-service/
kubectl apply -f admin-service/
# ... apply remaining services

# Apply infrastructure
kubectl apply -f postgres/
kubectl apply -f redis/
kubectl apply -f rabbitmq/
```

### HPA (Horizontal Pod Autoscaler)

All services include HPA manifests:
```bash
kubectl apply -f api-gateway/hpa.yaml
```

Minimum replicas: 2
Maximum replicas: 10
Target CPU: 70%

## Production Checklist

### Security
- [ ] All default passwords changed
- [ ] `SECRET_KEY` rotated (64-char random hex)
- [ ] `TENANT_DB_ENCRYPTION_KEY` rotated (32-byte base64)
- [ ] Keycloak realm configured with strong policies
- [ ] HTTPS enforced with valid certificates
- [ ] HSTS enabled
- [ ] CSP headers configured
- [ ] `.env` files not in version control
- [ ] Rate limiting enabled
- [ ] Brute-force protection active

### Infrastructure
- [ ] PostgreSQL with SSL
- [ ] Redis with authentication
- [ ] RabbitMQ with authentication
- [ ] Keycloak with HTTPS
- [ ] Backup strategy for databases
- [ ] Log aggregation (ELK/Loki)
- [ ] Monitoring (Prometheus/Grafana)
- [ ] Alerting configured

### Application
- [ ] Migrations applied (`alembic upgrade head`)
- [ ] Subscription plans synced to DB
- [ ] Health checks passing
- [ ] All services can communicate
- [ ] API Gateway routing table verified
- [ ] Streamlit frontend accessible

## Scaling

### Database
- Master DB: Single instance or primary-replica
- Tenant DBs: One per tenant, can be sharded by region
- Use connection pooling (PgBouncer)

### Services
- Stateless services can scale horizontally
- Use Redis for session/state caching
- RabbitMQ for event-driven scaling

### Recommended Instance Sizes

| Service | Dev | Small Prod | Large Prod |
|---------|-----|------------|------------|
| API Gateway | 1 | 2 | 4+ |
| Auth Service | 1 | 2 | 4+ |
| Master Service | 1 | 2 | 3 |
| Admin Service | 1 | 2 | 4+ |
| Clinical Services | 1 each | 2 each | 4+ each |

## Troubleshooting

### Service Won't Start
```bash
docker compose logs <service-name>
```

### Database Connection Issues
```bash
docker compose exec postgres-master psql -U postgres -c "\l"
```

### Keycloak Not Reachable
```bash
curl http://localhost:8080/realms/hospital-realm/.well-known/openid-configuration
```

### Redis Issues
```bash
docker compose exec redis redis-cli ping
```

### Migration Failures
```bash
docker compose exec auth-service python -m alembic -c migrations/tenant/alembic.ini upgrade head
```

## Health Endpoints

All services expose:
```
GET /health
```

Use for load balancer health checks and monitoring.

## Contact

For deployment support, contact the Hospital Flow DevOps team.
