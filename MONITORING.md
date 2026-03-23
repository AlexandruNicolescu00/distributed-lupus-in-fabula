# Monitoring Guide — Game Platform

## 1. Accessing the monitoring stack

### Docker Compose (development)

| Tool | URL | Credentials |
|------|-----|-------------|
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3001 | admin / admin |

### Kubernetes

| Tool | URL | Credentials |
|------|-----|-------------|
| Prometheus | http://game.local/prometheus | — |
| Grafana | http://game.local/grafana | admin / admin |

> If using port-forward instead of Ingress:
> ```powershell
> kubectl port-forward service/prometheus -n game 9090:9090
> kubectl port-forward service/grafana    -n game 3001:3000
> ```

---

## 2. Collected metrics

Metrics are scraped from two sources:

- **FastAPI backend** — exposed at `/metrics` on each pod (port 8000)
- **Redis Exporter** — exposed at `:9121/metrics`, reads internal Redis stats

### 2.1 HTTP metrics (automatic via `prometheus-fastapi-instrumentator`)

| Metric | Type | Description |
|--------|------|-------------|
| `http_request_duration_seconds` | Histogram | Request latency per HTTP path and method |
| `http_requests_total` | Counter | Total requests grouped by status code |
| `http_requests_in_progress` | Gauge | Requests currently being processed |

These are collected automatically without any code changes.

### 2.2 WebSocket metrics (custom — `backend/core/metrics.py`)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `ws_active_connections` | Gauge | `instance_id` | Active WebSocket connections on this instance |
| `ws_connections_total` | Counter | `instance_id` | Total accepted connections since startup |
| `ws_disconnections_total` | Counter | `instance_id`, `reason` | Disconnections by cause: `normal`, `timeout`, `error` |
| `ws_messages_received_total` | Counter | `instance_id`, `event_type` | Messages received from clients |
| `ws_messages_sent_total` | Counter | `instance_id`, `event_type` | Messages sent to clients |
| `ws_message_size_bytes` | Histogram | `instance_id` | Distribution of incoming message sizes |
| `ws_broadcast_duration_seconds` | Histogram | `instance_id` | Time to broadcast to all clients in a room |

### 2.3 Redis Pub/Sub metrics (custom — `backend/core/metrics.py`)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `redis_messages_published_total` | Counter | `instance_id`, `channel` | Messages published to Redis by this instance |
| `redis_messages_received_total` | Counter | `instance_id`, `channel` | Messages received from Redis (from other instances) |
| `redis_messages_deduplicated_total` | Counter | `instance_id` | Messages discarded by deduplication (self-originated) |
| `redis_publish_duration_seconds` | Histogram | `instance_id` | Latency of the Redis PUBLISH call |

### 2.4 Gameplay metrics (custom — `backend/core/metrics.py`)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `game_active_rooms` | Gauge | `instance_id` | Game rooms with at least one connected client |
| `game_active_players` | Gauge | `instance_id` | Players connected to this instance |

### 2.5 Redis infrastructure metrics (via `redis_exporter`)

| Metric | Description |
|--------|-------------|
| `redis_connected_clients` | Number of clients connected to Redis |
| `redis_commands_processed_total` | Commands processed per second |
| `redis_pubsub_channels` | Number of active Pub/Sub channels |
| `redis_memory_used_bytes` | Memory currently used by Redis |
| `redis_memory_max_bytes` | `maxmemory` configured limit |
| `redis_rdb_last_bgsave_status` | 1 = last RDB snapshot succeeded, 0 = failed |
| `redis_aof_enabled` | 1 = AOF persistence is active |

---

## 3. Grafana dashboard

The dashboard **Game Platform - Overview** is auto-provisioned at startup
from `infrastructure/monitoring/grafana/dashboards/game-platform.json`.
No manual import is needed.

Navigate to: **Dashboards → Game Platform - Overview**

### Dashboard panels

#### Row 1 — Summary (stat panels)

| Panel | Query | What to watch |
|-------|-------|---------------|
| Connected players (total) | `sum(game_active_players)` | Drop to 0 = all backends down |
| Active WebSocket connections | `sum(ws_active_connections)` | Should track player count closely |
| Active rooms | `sum(game_active_rooms)` | Sudden drop = possible crash |
| HTTP p99 latency | `histogram_quantile(0.99, ...)` | Alert fires above 500ms |
| 5xx error rate | `rate(5xx) / rate(total)` | Alert fires above 5% |
| Backend instances | `count(up{job="backend"} == 1)` | Must be >= 2 |

#### Row 2 — Players & WebSocket

Shows connected players and active WebSocket connections **per instance**,
plus connection/disconnection rates over time. Use this row to verify
that NGINX is distributing clients evenly across backend replicas.

**Healthy state:** all instances show similar connection counts.
**Warning sign:** one instance has significantly more connections than
others — indicates the load balancer is not distributing evenly.

#### Row 3 — Latency

| Panel | Percentiles shown | Healthy threshold |
|-------|-------------------|-------------------|
| HTTP latency | p50, p95, p99 | p99 < 200ms |
| Redis PUBLISH latency | p50, p95, p99 | p99 < 50ms |
| WebSocket broadcast latency (p95) | p95 per instance | < 100ms |
| HTTP throughput | 2xx, 4xx, 5xx req/s | 5xx near zero |

If p99 HTTP latency is high but Redis PUBLISH latency is low, the bottleneck
is in the backend event loop or the WebSocket broadcast phase.
If Redis PUBLISH latency is high, the bottleneck is the Redis broker itself.

#### Row 4 — Message Flow (Redis Pub/Sub)

| Panel | Description |
|-------|-------------|
| Published vs received | PUBLISH rate vs messages received from Redis per second |
| Deduplicated messages | Messages discarded by self-deduplication — should be ~= published rate |
| WS received vs sent | Client → backend vs backend → client message rates |
| Messages by event type | Breakdown of `player_action`, `player_joined`, etc. |
| Active Redis channels | Should equal the number of active game rooms |

**How to read the Pub/Sub flow panel:**
In normal operation, `published ≈ received × (N_instances - 1)` because each
message is published once and received by all other instances.
`deduplicated ≈ published` because each instance also receives its own
messages and discards them.

#### Row 5 — CPU & Resources

| Panel | Description |
|-------|-------------|
| CPU per instance (%) | `rate(process_cpu_seconds_total)` — alert fires above 85% |
| Memory RSS per instance | `process_resident_memory_bytes` |
| Current CPU gauge | Gauge bar with thresholds: green < 60%, yellow < 85%, red >= 85% |
| Redis memory used vs limit | Actual usage vs `maxmemory` configured limit |

---

## 4. Alerting rules

Rules live in `infrastructure/monitoring/prometheus/rules/` and are
loaded into Prometheus via a ConfigMap.

### backend.yml

| Alert | Condition | Severity | Description |
|-------|-----------|----------|-------------|
| `HighRequestLatency` | p99 > 500ms for 2m | warning | HTTP tail latency too high |
| `HighErrorRate` | 5xx rate > 5% for 1m | critical | Backend returning errors |
| `BackendDown` | `up{job="backend"} == 0` for 30s | critical | Instance unreachable by Prometheus |
| `HighWebSocketConnections` | total WS > 800 for 5m | warning | Consider scaling up |

### redis.yml

| Alert | Condition | Severity | Description |
|-------|-----------|----------|-------------|
| `RedisDown` | exporter unreachable for 30s | critical | Redis not reachable |
| `RedisHighMemoryUsage` | used / max > 80% for 2m | warning | Approaching memory limit |
| `RedisRDBSaveFailed` | last bgsave status = 0 for 5m | warning | Snapshot failed |

### infra.yml

| Alert | Condition | Severity | Description |
|-------|-----------|----------|-------------|
| `HighCPUUsage` | CPU > 85% for 5m | warning | Pod under heavy load |
| `LowBackendReplicas` | active instances < 2 for 1m | critical | HA guarantee broken |

### Viewing alerts

Navigate to **http://game.local/prometheus/alerts** (Kubernetes) or
**http://localhost:9090/alerts** (Docker Compose).

Each alert has three states:

| State | Meaning |
|-------|---------|
| `inactive` | Rule loaded, condition not met — normal |
| `pending` | Condition met but `for:` duration not elapsed yet |
| `firing` | Alert active — investigate |

### Updating alert rules without restart

```bash
# macOS/Linux
./k8s/update-rules.sh
```

```powershell
# Windows PowerShell
kubectl create configmap prometheus-rules `
  --from-file=infrastructure/monitoring/prometheus/rules/ `
  -n game --dry-run=client -o yaml | kubectl apply -f -

kubectl exec -n game deploy/prometheus -- `
  wget -q --post-data='' http://localhost:9090/prometheus/-/reload -O -
```

---

## 5. Reading the dashboard — anomaly detection guide

### Scenario 1 — Backend instance down

**Symptoms:**
- Stat panel "Backend instances" drops below 2 (turns red)
- `LowBackendReplicas` alert fires
- Player count drops on the affected instance panel

**Investigation:**
```powershell
kubectl get pods -n game -l app=backend
kubectl logs <crashed-pod> -n game --previous
kubectl describe pod <crashed-pod> -n game
```

**Expected resolution:** Kubernetes restarts the pod automatically within
~30 seconds. If it keeps crashing, check the logs for import errors or
Redis connectivity issues.

---

### Scenario 2 — High latency spike

**Symptoms:**
- HTTP p99 turns yellow/red on the latency panel
- `HighRequestLatency` alert fires

**Investigation steps:**
1. Check the **Redis PUBLISH latency** panel — if it spikes too, the bottleneck
   is Redis or the network between pods.
2. Check the **CPU per instance** panel — if CPU is near 100%, the event loop
   is saturated; HPA should trigger a scale-up.
3. Check `kubectl get hpa -n game` — verify that scaling is happening.

**Typical cause in development:** minikube has limited CPU. Latency spikes
are expected above ~100 concurrent WebSocket connections on a single-node
cluster.

---

### Scenario 3 — Redis memory pressure

**Symptoms:**
- "Redis memory used vs limit" panel approaches the red threshold
- `RedisHighMemoryUsage` alert fires

**Investigation:**
```powershell
kubectl exec -n game deploy/redis -- redis-cli info memory
kubectl exec -n game deploy/redis -- redis-cli info keyspace
```

**Root cause:** the `game:state:<room_id>` keys (TTL 3600s) or
`game:players:<room_id>` sets are accumulating. Check if rooms are
being cleaned up correctly after all players disconnect.

---

### Scenario 4 — Pub/Sub deduplication anomaly

**Symptoms:**
- On the **Message Flow** panel: `deduplicated` rate is significantly
  lower than `published` rate

**Meaning:** some messages are not being deduplicated — a backend instance
may be forwarding its own messages to its local clients twice (once via
direct broadcast, once via the Redis listener).

**Investigation:**
```powershell
kubectl logs -n game -l app=backend --tail=100 | Select-String "Dedup"
```

Check `INSTANCE_ID` is being set correctly from `HOSTNAME` in the pod spec.

---

## 6. Scraping configuration reference

### Docker Compose (`prometheus.dev.yml`)

| Job | Target | Interval |
|-----|--------|----------|
| `backend` | `host.docker.internal:8000` | 10s |
| `redis` | `redis_exporter:9121` | 15s |
| `prometheus` | `localhost:9090` | 15s |

### Kubernetes (`prometheus.yml`)

| Job | Discovery | Interval |
|-----|-----------|----------|
| `backend` | `kubernetes_sd_configs` (pod annotations) | 10s |
| `redis` | `redis-exporter.game.svc.cluster.local:9121` | 15s |
| `prometheus` | `localhost:9090` | 15s |

The backend uses annotation-based discovery: Prometheus finds all pods
labeled `app=backend` that have `prometheus.io/scrape: "true"` and scrapes
each one individually. This means each pod appears as a separate target,
identified by its pod name (`instance` label = `metadata.name`).