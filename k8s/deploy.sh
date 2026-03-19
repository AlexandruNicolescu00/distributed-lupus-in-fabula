#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Applica tutti i manifest Kubernetes nell'ordine corretto.
# Uso:
#   ./k8s/deploy.sh           → deploy completo
#   ./k8s/deploy.sh --delete  → rimuove tutte le risorse
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

K8S_DIR="$(cd "$(dirname "$0")" && pwd)"
NAMESPACE="game"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
step() { echo -e "\n${YLW}▸ $1${NC}"; }
ok()   { echo -e "  ${GRN}✓${NC} $1"; }
err()  { echo -e "  ${RED}✗${NC} $1"; exit 1; }

# ── Delete mode ───────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--delete" ]]; then
  echo -e "${RED}Rimozione di tutte le risorse nel namespace '$NAMESPACE'...${NC}"
  kubectl delete namespace "$NAMESPACE" --ignore-not-found
  echo -e "${GRN}Namespace rimosso.${NC}"
  exit 0
fi

# ── Prerequisiti ──────────────────────────────────────────────────────────────
step "Verifica prerequisiti"
command -v kubectl &>/dev/null || err "kubectl non trovato"
kubectl cluster-info &>/dev/null  || err "Nessun cluster raggiungibile"
ok "kubectl disponibile e cluster raggiungibile"

# ── Namespace ─────────────────────────────────────────────────────────────────
step "Namespace"
kubectl apply -f "$K8S_DIR/namespace.yml"
ok "Namespace '$NAMESPACE' pronto"

# ── ConfigMap e Secret ────────────────────────────────────────────────────────
step "ConfigMap e Secret"
kubectl apply -f "$K8S_DIR/configmap.yml"
ok "ConfigMap applicato"
# Secret: applicare solo se esiste il file (non il .example)
if [[ -f "$K8S_DIR/secret.yml" ]]; then
  kubectl apply -f "$K8S_DIR/secret.yml"
  ok "Secret applicato"
else
  echo -e "  ${YLW}→${NC} secret.yml non trovato — saltato (ok se Redis non ha password)"
fi

# ── Redis ─────────────────────────────────────────────────────────────────────
step "Redis"
kubectl apply -f "$K8S_DIR/redis/"
ok "Redis deployment, service e PVC applicati"

echo "  Attesa Redis ready..."
kubectl rollout status deployment/redis -n "$NAMESPACE" --timeout=90s
ok "Redis pronto"

# ── Backend ───────────────────────────────────────────────────────────────────
step "Backend FastAPI"
kubectl apply -f "$K8S_DIR/backend/"
ok "Backend deployment, service e HPA applicati"

echo "  Attesa Backend ready..."
kubectl rollout status deployment/backend -n "$NAMESPACE" --timeout=120s
ok "Backend pronto"

# ── Frontend ──────────────────────────────────────────────────────────────────
step "Frontend Vue"
kubectl apply -f "$K8S_DIR/frontend/"
ok "Frontend deployment e service applicati"

echo "  Attesa Frontend ready..."
kubectl rollout status deployment/frontend -n "$NAMESPACE" --timeout=90s
ok "Frontend pronto"

# ── Ingress ───────────────────────────────────────────────────────────────────
step "Ingress"
kubectl apply -f "$K8S_DIR/ingress.yml"
ok "Ingress applicato"

# ── Riepilogo ─────────────────────────────────────────────────────────────────
echo -e "\n${GRN}══════════════════════════════════════════${NC}"
echo -e "${GRN}  Deploy completato${NC}"
echo -e "${GRN}══════════════════════════════════════════${NC}"

echo -e "\nRisorse nel namespace '$NAMESPACE':"
kubectl get all -n "$NAMESPACE"

echo -e "\nIngress:"
kubectl get ingress -n "$NAMESPACE"

echo -e "\n${YLW}Per testare in locale con minikube:${NC}"
echo "  minikube ip   # ottieni l'IP del cluster"
echo "  # Aggiungi a /etc/hosts:"
echo "  #   <minikube-ip>  game.local"
echo "  # Poi apri: http://game.local"
echo "  # WebSocket: ws://game.local/ws/<room_id>"