# backend/core/instance.py
# ─────────────────────────────────────────────────────────────────────────────
# Modulo indipendente che espone INSTANCE_ID.
# Separato da pubsub/manager.py per evitare import circolari:
#
#   PRIMA (circolare):
#     pubsub/manager.py  →  websocket/connection_manager.py  →  pubsub/manager.py ✗
#
#   ORA (aciclico):
#     pubsub/manager.py         →  core/instance.py  ✓
#     websocket/connection_manager.py  →  core/instance.py  ✓
# ─────────────────────────────────────────────────────────────────────────────
import os
import uuid

# In Kubernetes il nome del pod viene iniettato come HOSTNAME (vedi deployment.yml).
# In Docker Compose ogni container ha il proprio HOSTNAME generato da Docker.
# In locale (uvicorn diretto) usa le prime 8 cifre di un UUID casuale.
INSTANCE_ID: str = os.environ.get("HOSTNAME", str(uuid.uuid4())[:8])
