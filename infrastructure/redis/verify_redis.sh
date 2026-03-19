#!/usr/bin/env bash
# verify_redis.sh
# Verifica che Redis sia configurato correttamente con RDB + AOF abilitati.
# Uso: ./verify_redis.sh [host] [port]
# Default: localhost 6379

set -euo pipefail

HOST=${1:-localhost}
PORT=${2:-6379}
CLI="redis-cli -h $HOST -p $PORT"

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "  ${GRN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; ERRORS=$((ERRORS+1)); }
info() { echo -e "  ${YLW}→${NC} $1"; }

ERRORS=0

echo ""
echo "══════════════════════════════════════════"
echo "  Redis Config Verification"
echo "  $HOST:$PORT"
echo "══════════════════════════════════════════"

# ── Connettività ─────────────────────────────
echo ""
echo "▸ Connettività"
if $CLI ping | grep -q PONG; then
  pass "Redis raggiungibile (PING → PONG)"
else
  fail "Redis non raggiungibile su $HOST:$PORT"
  exit 1
fi

# ── RDB ──────────────────────────────────────
echo ""
echo "▸ Persistenza RDB"

RDB_ENABLED=$($CLI config get save | tail -1)
if [ -n "$RDB_ENABLED" ]; then
  pass "RDB abilitato: $RDB_ENABLED"
else
  fail "RDB non configurato (save è vuoto)"
fi

RDBFILE=$($CLI config get dbfilename | tail -1)
pass "File RDB: $RDBFILE"

RDBDIR=$($CLI config get dir | tail -1)
pass "Directory dati: $RDBDIR"

# ── AOF ──────────────────────────────────────
echo ""
echo "▸ Persistenza AOF"

AOF=$($CLI config get appendonly | tail -1)
if [ "$AOF" = "yes" ]; then
  pass "AOF abilitato"
else
  fail "AOF non abilitato (appendonly = $AOF)"
fi

AOFSYNC=$($CLI config get appendfsync | tail -1)
if [ "$AOFSYNC" = "everysec" ] || [ "$AOFSYNC" = "always" ]; then
  pass "appendfsync: $AOFSYNC"
else
  fail "appendfsync subottimale: $AOFSYNC"
fi

AOF_PREAMBLE=$($CLI config get aof-use-rdb-preamble | tail -1)
if [ "$AOF_PREAMBLE" = "yes" ]; then
  pass "AOF RDB preamble abilitato (riavvio veloce)"
else
  info "AOF RDB preamble disabilitato (riavvio più lento su dataset grandi)"
fi

# ── Memoria ───────────────────────────────────
echo ""
echo "▸ Configurazione memoria"

MAXMEM=$($CLI config get maxmemory | tail -1)
if [ "$MAXMEM" != "0" ]; then
  pass "maxmemory configurato: $MAXMEM bytes"
else
  info "maxmemory non impostato (Redis usa tutta la RAM disponibile)"
fi

EVICTION=$($CLI config get maxmemory-policy | tail -1)
pass "Politica eviction: $EVICTION"

# ── Test scrittura + BGSAVE ───────────────────
echo ""
echo "▸ Test scrittura e snapshot"

TEST_KEY="redis_verify_$(date +%s)"
$CLI set "$TEST_KEY" "ok" EX 30 > /dev/null
VAL=$($CLI get "$TEST_KEY")
if [ "$VAL" = "ok" ]; then
  pass "Scrittura/lettura chiave test riuscita"
else
  fail "Errore nella scrittura/lettura"
fi

BGSAVE=$($CLI bgsave)
if echo "$BGSAVE" | grep -qi "background saving started\|already in progress"; then
  pass "BGSAVE avviato con successo"
else
  info "BGSAVE: $BGSAVE"
fi

# ── Test Pub/Sub ──────────────────────────────
echo ""
echo "▸ Test Pub/Sub"

# Sottoscrivi in background per 2 secondi
(timeout 2 $CLI subscribe test_channel > /tmp/redis_sub_out 2>&1 || true) &
SUB_PID=$!
sleep 0.3

# Pubblica un messaggio
PUB_RESULT=$($CLI publish test_channel "hello")
sleep 0.5

if [ "$PUB_RESULT" -ge "0" ] 2>/dev/null; then
  pass "PUBLISH riuscito ($PUB_RESULT subscriber/s attivi)"
else
  fail "PUBLISH fallito"
fi

wait $SUB_PID 2>/dev/null || true

# ── Info finali ───────────────────────────────
echo ""
echo "▸ Riepilogo istanza"

REDIS_VERSION=$($CLI info server | grep redis_version | cut -d: -f2 | tr -d '\r')
UPTIME=$($CLI info server | grep uptime_in_seconds | cut -d: -f2 | tr -d '\r')
CONNECTED=$($CLI info clients | grep connected_clients | cut -d: -f2 | tr -d '\r')
USED_MEM=$($CLI info memory | grep used_memory_human | cut -d: -f2 | tr -d '\r')

info "Versione Redis:     $REDIS_VERSION"
info "Uptime (secondi):   $UPTIME"
info "Client connessi:    $CONNECTED"
info "Memoria utilizzata: $USED_MEM"

# ── Risultato ─────────────────────────────────
echo ""
echo "══════════════════════════════════════════"
if [ "$ERRORS" -eq 0 ]; then
  echo -e "  ${GRN}Tutti i check superati${NC}"
else
  echo -e "  ${RED}$ERRORS check falliti — verificare la configurazione${NC}"
fi
echo "══════════════════════════════════════════"
echo ""

exit $ERRORS