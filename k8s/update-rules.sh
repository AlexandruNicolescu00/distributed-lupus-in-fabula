#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Aggiorna le regole di alerting Prometheus senza rideploy.
# Unica fonte di verità: infrastructure/monitoring/prometheus/rules/
#
# Uso:
#   ./k8s/update-rules.sh
#
# Su Windows (PowerShell):
#   bash k8s/update-rules.sh
#   # oppure eseguire i comandi manualmente (vedi sotto)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RULES_DIR="$SCRIPT_DIR/../infrastructure/monitoring/prometheus/rules"
NAMESPACE="game"

GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'

echo -e "${YLW}▸ Aggiornamento regole Prometheus${NC}"
echo -e "  Sorgente: $RULES_DIR"

# Ricrea il ConfigMap dai file sorgente (--dry-run + apply = upsert)
kubectl create configmap prometheus-rules \
  --from-file="$RULES_DIR/" \
  -n "$NAMESPACE" \
  --dry-run=client -o yaml | kubectl apply -f -

echo -e "${YLW}▸ Ricarica configurazione Prometheus (hot-reload)${NC}"

# Prometheus supporta il reload via HTTP senza riavvio del pod
# (richiede --web.enable-lifecycle, già configurato)
kubectl exec -n "$NAMESPACE" deploy/prometheus -- \
  wget -q --post-data='' \
  "http://localhost:9090/prometheus/-/reload" \
  -O - 2>/dev/null && echo "  Reload inviato" || echo "  Reload fallito — prova: kubectl rollout restart deployment/prometheus -n $NAMESPACE"

echo -e "${GRN}✓ Regole aggiornate${NC}"
echo ""
echo "Verifica su Prometheus: http://game.local/prometheus/alerts"