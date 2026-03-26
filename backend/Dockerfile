# ─────────────────────────────────────────────────────────────────────────────
# Dockerfile — Backend FastAPI
# Build multi-stage: dipendenze separate dal runtime per immagine minima
# ─────────────────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════
# STAGE 1 — builder
# Installa le dipendenze Python in un ambiente isolato
# ══════════════════════════════════════════════════════════════
FROM python:3.12-slim AS builder

WORKDIR /app

# Evita che Python scriva .pyc e bufferizzi stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Installa build dependencies (necessarie per compilare pacchetti C come uvloop)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copia solo il file delle dipendenze prima del codice
# (sfrutta la cache Docker: se requirements.txt non cambia, questo layer è cached)
COPY requirements.txt .

# Installa le dipendenze in una directory dedicata
RUN pip install --upgrade pip && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt


# ══════════════════════════════════════════════════════════════
# STAGE 2 — runtime
# Immagine finale minimale, senza build tools
# ══════════════════════════════════════════════════════════════
FROM python:3.12-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Aggiunge /install/lib al path Python
    PYTHONPATH=/install/lib/python3.12/site-packages

# Copia le dipendenze compilate dallo stage builder
COPY --from=builder /install /install

# Copia il codice sorgente dell'applicazione
COPY . .

# Utente non-root per sicurezza
RUN addgroup --system appgroup && \
    adduser --system --ingroup appgroup appuser && \
    chown -R appuser:appgroup /app

USER appuser

# Porta esposta dal server ASGI
EXPOSE 8000

# Health check interno al container
HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Avvia Uvicorn con:
#   --host 0.0.0.0       → ascolta su tutte le interfacce
#   --workers 1          → 1 worker per container (scaling orizzontale via K8s)
#   --loop uvloop        → event loop performante per WebSocket
#   --ws websockets      → libreria WebSocket (alternativa: wsproto)
#   --proxy-headers      → rispetta X-Forwarded-For da NGINX
#   --forwarded-allow-ips → accetta proxy headers da qualsiasi IP interno
# Uvicorn serve l'app composta FastAPI + Socket.IO.
# Socket.IO gestisce internamente il transport WebSocket.
CMD ["python", "-m", "uvicorn", "main:asgi_app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--loop", "uvloop", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]