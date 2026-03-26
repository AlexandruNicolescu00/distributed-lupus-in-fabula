# Distributed-lupus-in-fabula

Distributed Systems Course Project, University of Bologna, 2025-2026

## Deployment Guide — Game Platform

## Project Structure

```
.
├── backend/                        # FastAPI + Redis Pub/Sub
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   └── core/
│       ├── config.py
│       ├── instance.py
│       ├── messages.py
│       ├── metrics.py
│       └── state_store.py
├── frontend/                       # Vue 3 + Vite
│   ├── Dockerfile
│   └── nginx.conf
├── infrastructure/
│   ├── nginx/                      # Reverse proxy
│   │   ├── nginx.conf
│   │   └── conf.d/game.conf
│   └── monitoring/
│       ├── prometheus/
│       │   ├── prometheus.yml
│       │   ├── prometheus.dev.yml
│       │   └── rules/
│       └── grafana/
│           ├── provisioning/
│           └── dashboards/
├── k8s/                            # Kubernetes manifests
│   ├── deploy.sh
│   ├── namespace.yml
│   ├── configmap.yml
│   ├── ingress.yml
│   ├── backend/
│   ├── frontend/
│   ├── redis/
│   └── monitoring/
├── tests/
├── docker-compose.yml              # Full environment
└── docker-compose-dev.yml          # Support services only
```

---

## Part 1 — Local Setup with Docker Compose

### Prerequisites

| Tool | Minimum version | Check |
|------|----------------|-------|
| Docker Desktop | 24.x | `docker --version` |
| Docker Compose | v2.x | `docker compose version` |

### 1.1 First-time setup

```bash
# Clone the repository
git clone <repo-url>
cd <repo-dir>

# Create the .env file from the template
cp backend/.env.example backend/.env
# Edit backend/.env if needed (REDIS_URL is already configured for Docker)
```

### 1.2 Development mode — support services only

Use this when backend and frontend run directly on the developer's machine
(uvicorn + vite dev server), without containers.

```bash
# Start Redis, Redis Exporter, Prometheus, Grafana
docker compose -f docker-compose-dev.yml up -d

# Verify all services are up
docker compose -f docker-compose-dev.yml ps
```

In a second terminal, start the backend:

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

In a third terminal, start the frontend:

```bash
cd frontend
npm install
npm run dev   # vite dev server at http://localhost:5173
```

Available services:

| Service | URL |
|---------|-----|
| Backend API | http://localhost:8000 |
| Backend WebSocket | ws://localhost:8000/ws/\<room_id\> |
| Frontend | http://localhost:5173 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 (admin/admin) |
| Redis | localhost:6379 |

To stop support services:

```bash
docker compose -f docker-compose-dev.yml down
```

### 1.3 Full mode — all services containerised

```bash
# Build images
docker compose build

# Start all services with 2 backend replicas
docker compose up -d --scale backend=2

# Check status
docker compose ps

# Verify Redis connectivity
chmod +x redis/verify_redis.sh
./redis/verify_redis.sh localhost 6379
```

Available services:

| Service | URL |
|---------|-----|
| Frontend | http://localhost |
| WebSocket | ws://localhost/ws/\<room_id\> |
| API | http://localhost/api/ |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 (admin/admin) |

### 1.4 Useful Docker Compose commands

```bash
# Live logs for all services
docker compose logs -f

# Logs for a specific service
docker compose logs -f backend

# Scale the backend to 3 instances
docker compose up -d --scale backend=3

# Restart a single service
docker compose restart nginx

# Stop everything, keep volumes (Redis data preserved)
docker compose down

# Stop everything and remove volumes (Redis data deleted)
docker compose down -v

# Rebuild and restart a single service
docker compose up -d --build backend
```

### 1.5 Smoke test

```bash
# Backend health check
curl http://localhost/health

# WebSocket connection test (requires wscat: npm install -g wscat)
wscat -c "ws://localhost/ws/test_room?client_id=test123"

# Send a test message
# > {"event_type": "player_action", "room_id": "test_room", "payload": {"test": true}}
```

---

## Part 2 — Kubernetes Cluster Deployment

### Prerequisites

| Tool | Minimum version | Installation |
|------|----------------|--------------|
| kubectl | 1.28+ | `winget install Kubernetes.kubectl` |
| minikube | 1.32+ | `winget install Kubernetes.minikube` |
| Docker Desktop | 24.x | Required as minikube driver |

> **Windows:** all commands in Part 2 are PowerShell.
> **macOS/Linux:** replace PowerShell backticks with `\` for multi-line commands.

### 2.1 Minikube setup (first time only)

```powershell
# Start the cluster with adequate resources
minikube start --cpus=4 --memory=4096 --driver=docker

# Enable required addons
minikube addons enable ingress        # NGINX ingress controller
minikube addons enable metrics-server # required by HPA

# Verify
kubectl get nodes
kubectl get pods -n ingress-nginx
```

### 2.2 Point Docker to minikube's registry

Images must be visible to minikube. Run this command **before every build
session** (it resets when the terminal is closed):

```powershell
& minikube -p minikube docker-env --shell powershell | Invoke-Expression
```

To make it permanent (optional):

```powershell
Add-Content -Path $PROFILE `
  -Value '& minikube -p minikube docker-env --shell powershell | Invoke-Expression'
```

### 2.3 Build images

```powershell
docker build -t game_backend:latest  ./backend
docker build -t game_frontend:latest ./frontend

# Verify images are visible to minikube
minikube image ls | Select-String "game_"
```

### 2.4 Full deploy

```powershell
# Create the Prometheus rules ConfigMap (single source of truth)
kubectl create configmap prometheus-rules `
  --from-file=infrastructure/monitoring/prometheus/rules/ `
  -n game `
  --dry-run=client -o yaml | kubectl apply -f -

# Create the Grafana dashboard ConfigMap
kubectl create configmap grafana-dashboards `
  --from-file=game-platform.json=infrastructure/monitoring/grafana/dashboards/game-platform.json `
  -n game `
  --dry-run=client -o yaml | kubectl apply -f -

# Deploy all resources in order
kubectl apply -f k8s/namespace.yml
kubectl apply -f k8s/configmap.yml
kubectl apply -f k8s/redis/
kubectl rollout status deployment/redis -n game --timeout=90s

kubectl apply -f k8s/backend/
kubectl rollout status deployment/backend -n game --timeout=120s

kubectl apply -f k8s/frontend/
kubectl rollout status deployment/frontend -n game --timeout=90s

kubectl apply -f k8s/monitoring/redis-exporter.yml
kubectl apply -f k8s/monitoring/prometheus.yml
kubectl apply -f k8s/monitoring/grafana.yml
kubectl rollout status deployment/prometheus -n game --timeout=120s
kubectl rollout status deployment/grafana -n game --timeout=120s

kubectl apply -f k8s/ingress.yml
```

Alternatively, use the automated script (macOS/Linux/WSL):

```bash
chmod +x k8s/deploy.sh
./k8s/deploy.sh
```

### 2.5 Configure local access

```powershell
# Separate terminal — keep it open for the entire session
minikube tunnel

# In another terminal: add game.local to hosts
# (requires PowerShell as administrator)
Add-Content -Path "C:\Windows\System32\drivers\etc\hosts" `
  -Value "127.0.0.1  game.local"
```

Available services after deployment:

| Service | URL |
|---------|-----|
| Frontend | http://game.local |
| WebSocket | ws://game.local/ws/\<room_id\> |
| API | http://game.local/api/ |
| Prometheus | http://game.local/prometheus |
| Grafana | http://game.local/grafana (admin/admin) |

### 2.6 Verify deployment status

```powershell
# Overview of all pods
kubectl get pods -n game

# Expected output:
# NAME                             READY   STATUS    RESTARTS
# backend-xxxx-xxxx                1/1     Running   0
# backend-xxxx-yyyy                1/1     Running   0
# frontend-xxxx-xxxx               1/1     Running   0
# frontend-xxxx-yyyy               1/1     Running   0
# grafana-xxxx-xxxx                1/1     Running   0
# prometheus-xxxx-xxxx             1/1     Running   0
# redis-xxxx-xxxx                  1/1     Running   0
# redis-exporter-xxxx-xxxx         1/1     Running   0

# Services and addresses
kubectl get services -n game

# Ingress
kubectl get ingress -n game

# HPA (autoscaling)
kubectl get hpa -n game

# HTTP health check
curl http://game.local/health
```

### 2.7 Restart after a machine reboot

```powershell
# 1. Start Docker Desktop and wait until it is fully ready

# 2. Start minikube
minikube start

# 3. Start the tunnel (separate terminal, keep it open)
minikube tunnel

# 4. Wait for pods to come back (1-2 minutes)
kubectl get pods -n game

# If any pod is stuck, force a restart
kubectl rollout restart deployment -n game
```

### 2.8 Useful kubectl commands

```powershell
# Stream logs from a deployment
kubectl logs deployment/backend -n game --follow

# Logs from a specific pod
kubectl logs <pod-name> -n game

# Open a shell inside a container
kubectl exec -it deployment/backend -n game -- /bin/sh

# Describe a pod (useful for diagnosing crashes)
kubectl describe pod <pod-name> -n game

# Show namespace events sorted by time
kubectl get events -n game --sort-by='.lastTimestamp'

# Delete a pod (triggers a rolling replacement)
kubectl delete pod <pod-name> -n game

# Update a ConfigMap and restart the affected deployment
kubectl apply -f k8s/configmap.yml
kubectl rollout restart deployment/backend -n game
```

---

## Part 3 — Scaling

### 3.1 Manual scaling

```powershell
# Scale the backend to 3 replicas
kubectl scale deployment/backend -n game --replicas=3

# Verify
kubectl get pods -n game -l app=backend

# Scale back to 2 replicas
kubectl scale deployment/backend -n game --replicas=2
```

### 3.2 Automatic scaling with HPA

The HPA is already configured in `k8s/backend/hpa.yml`:

| Parameter | Value |
|-----------|-------|
| Minimum replicas | 2 |
| Maximum replicas | 8 |
| CPU target | 60% |
| Memory target | 75% |
| Scale-up stabilization | 15s |
| Scale-down stabilization | 300s |

```powershell
# Current HPA status
kubectl get hpa -n game

# Example output:
# NAME          REFERENCE             TARGETS    MINPODS  MAXPODS  REPLICAS
# backend-hpa   Deployment/backend    12%/60%    2        8        2

# Live monitoring during a load test
kubectl get hpa -n game -w

# Detailed description (includes scaling events)
kubectl describe hpa backend-hpa -n game
```

Scale-down is intentionally slow (300s) to avoid disconnecting active
WebSocket clients. A scaling event appears in the logs as:

```
Normal  SuccessfulRescale  backend-hpa  New size: 4; reason: cpu resource utilization
```

### 3.3 Image updates (rolling update)

```powershell
# Rebuild the image
& minikube -p minikube docker-env --shell powershell | Invoke-Expression
docker build -t game_backend:latest ./backend

# Rolling update with zero downtime (maxUnavailable: 0 in the Deployment)
kubectl rollout restart deployment/backend -n game

# Monitor progress
kubectl rollout status deployment/backend -n game

# Roll back if something goes wrong
kubectl rollout undo deployment/backend -n game
```

### 3.4 Update Prometheus alerting rules

Alerting rules live in `infrastructure/monitoring/prometheus/rules/`.
To update them without restarting Prometheus:

```bash
# macOS/Linux
./k8s/update-rules.sh
```

```powershell
# Windows PowerShell
kubectl create configmap prometheus-rules `
  --from-file=infrastructure/monitoring/prometheus/rules/ `
  -n game `
  --dry-run=client -o yaml | kubectl apply -f -

kubectl exec -n game deploy/prometheus -- `
  wget -q --post-data='' http://localhost:9090/prometheus/-/reload -O -
```

### 3.5 Full cleanup

```powershell
# Remove all namespace resources (Redis data included)
kubectl delete namespace game

# Or use the script
bash k8s/deploy.sh --delete

# Start completely fresh with minikube
minikube delete
minikube start --cpus=4 --memory=4096 --driver=docker
```