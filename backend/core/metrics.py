# ─────────────────────────────────────────────────────────────────────────────
# Metriche Prometheus custom esposte dal backend FastAPI.
#
# prometheus-fastapi-instrumentator copre automaticamente le metriche HTTP
# standard (latenza, throughput, status codes).
# Questo modulo aggiunge le metriche specifiche per WebSocket e gameplay.
#
# Tutte le metriche sono registrate sul registry globale di Prometheus
# e vengono esposte sull'endpoint /metrics già configurato in main.py.
# ─────────────────────────────────────────────────────────────────────────────

from prometheus_client import Counter, Gauge, Histogram

# ── Connessioni WebSocket ─────────────────────────────────────────────────────

WS_ACTIVE_CONNECTIONS = Gauge(
    name="ws_active_connections",
    documentation="Numero di connessioni WebSocket attive su questa istanza",
    labelnames=["instance_id"],
)

WS_CONNECTIONS_TOTAL = Counter(
    name="ws_connections_total",
    documentation="Totale connessioni WebSocket accettate dall'avvio",
    labelnames=["instance_id"],
)

WS_DISCONNECTIONS_TOTAL = Counter(
    name="ws_disconnections_total",
    documentation="Totale disconnessioni WebSocket dall'avvio",
    labelnames=["instance_id", "reason"],
    # reason: "normal" | "timeout" | "error"
)

# ── Messaggi WebSocket ────────────────────────────────────────────────────────

WS_MESSAGES_RECEIVED_TOTAL = Counter(
    name="ws_messages_received_total",
    documentation="Messaggi ricevuti dai client WebSocket",
    labelnames=["instance_id", "event_type"],
)

WS_MESSAGES_SENT_TOTAL = Counter(
    name="ws_messages_sent_total",
    documentation="Messaggi inviati ai client WebSocket",
    labelnames=["instance_id", "event_type"],
)

WS_MESSAGE_SIZE_BYTES = Histogram(
    name="ws_message_size_bytes",
    documentation="Dimensione in byte dei messaggi WebSocket ricevuti",
    labelnames=["instance_id"],
    buckets=[64, 256, 512, 1024, 4096, 16384, 65536],
)

# ── Redis Pub/Sub ─────────────────────────────────────────────────────────────

REDIS_MESSAGES_PUBLISHED_TOTAL = Counter(
    name="redis_messages_published_total",
    documentation="Messaggi pubblicati su Redis Pub/Sub da questa istanza",
    labelnames=["instance_id", "channel"],
)

REDIS_MESSAGES_RECEIVED_TOTAL = Counter(
    name="redis_messages_received_total",
    documentation="Messaggi ricevuti da Redis Pub/Sub su questa istanza",
    labelnames=["instance_id", "channel"],
)

REDIS_MESSAGES_DEDUPLICATED_TOTAL = Counter(
    name="redis_messages_deduplicated_total",
    documentation="Messaggi Redis scartati per deduplicazione (origine locale)",
    labelnames=["instance_id"],
)

# ── Stanze e player ───────────────────────────────────────────────────────────

ACTIVE_ROOMS = Gauge(
    name="game_active_rooms",
    documentation="Stanze di gioco con almeno un client connesso su questa istanza",
    labelnames=["instance_id"],
)

ACTIVE_PLAYERS = Gauge(
    name="game_active_players",
    documentation="Player connessi su questa istanza (somma di tutti i client)",
    labelnames=["instance_id"],
)

# ── Latenza interna ───────────────────────────────────────────────────────────

REDIS_PUBLISH_DURATION_SECONDS = Histogram(
    name="redis_publish_duration_seconds",
    documentation="Latenza del PUBLISH Redis (dal backend al broker)",
    labelnames=["instance_id"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5],
)

WS_BROADCAST_DURATION_SECONDS = Histogram(
    name="ws_broadcast_duration_seconds",
    documentation="Tempo impiegato per fare broadcast a tutti i client di una stanza",
    labelnames=["instance_id"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5],
)
