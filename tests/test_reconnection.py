#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# Verifica che un client che si riconnette (anche su istanza diversa) riceva
# lo stato di gioco aggiornato e coerente.
#
# Scenari testati:
#   1. Stato alla prima connessione: client riceve game_state_sync se esiste
#      stato precedente nella stanza
#   2. Riconnessione stessa istanza: il client riconnesso riceve lo stato
#      aggiornato prodotto mentre era disconnesso
#   3. Riconnessione su istanza diversa: lo stato arriva correttamente anche
#      se il client riatterra su un pod diverso
#   4. Consistenza stato: due client su istanze diverse vedono lo stesso stato
#   5. Registro player: la lista dei player è coerente dopo disconnessioni
#
# Uso:
#   # Via Ingress (test 1, 2, 4, 5)
#   python tests/test_reconnection.py --url ws://game.local/ws
#
#   # Modalità diretta con port-forward (tutti i test incluso il 3)
#   python tests/test_reconnection.py \
#     --pod-a ws://localhost:8001/ws \
#     --pod-b ws://localhost:8002/ws
# ─────────────────────────────────────────────────────────────────────────────

import argparse
import asyncio
import json
import sys
import time
import uuid
from typing import Optional

try:
    import websockets
except ImportError:
    print("Installa le dipendenze: pip install websockets")
    sys.exit(1)

GRN = "\033[0;32m"
RED = "\033[0;31m"
YLW = "\033[1;33m"
BLU = "\033[0;34m"
NC = "\033[0m"


def ok(msg):
    print(f"  {GRN}✓{NC} {msg}")


def fail(msg):
    print(f"  {RED}✗{NC} {msg}")


def info(msg):
    print(f"  {BLU}→{NC} {msg}")


def step(msg):
    print(f"\n{YLW}▸ {msg}{NC}")


# ── Client helper (stessa base di test_multi_instance.py) ─────────────────────


class WSClient:
    def __init__(self, name: str, url: str, client_id: Optional[str] = None):
        self.name = name
        self.url = url
        self.client_id = client_id or f"test_{uuid.uuid4().hex[:8]}"
        self.received: asyncio.Queue = asyncio.Queue()
        self._ws = None
        self._task = None

    async def connect(self) -> None:
        full_url = f"{self.url}?client_id={self.client_id}"
        self._ws = await websockets.connect(full_url, open_timeout=10)
        self._task = asyncio.create_task(self._recv_loop(), name=f"recv-{self.name}")
        info(f"{self.name} connesso | client_id={self.client_id}")

    async def reconnect(self) -> None:
        """Chiude e riapre la connessione mantenendo lo stesso client_id."""
        await self.close()
        await asyncio.sleep(0.5)
        await self.connect()
        info(f"{self.name} riconnesso | client_id={self.client_id}")

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                await self.received.put(json.loads(raw))
        except websockets.ConnectionClosed:
            pass

    async def send(self, event_type: str, payload: dict = None) -> None:
        room_id = self.url.split("/ws/")[-1].split("?")[0]
        msg = {"event_type": event_type, "room_id": room_id, "payload": payload or {}}
        await self._ws.send(json.dumps(msg))

    async def wait_for(
        self,
        event_type: str,
        timeout: float = 5.0,
        test_id: Optional[str] = None,
    ) -> Optional[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                msg = await asyncio.wait_for(self.received.get(), timeout=remaining)
                if msg.get("event_type") != event_type:
                    continue
                if test_id and msg.get("payload", {}).get("test_id") != test_id:
                    continue
                return msg
            except asyncio.TimeoutError:
                return None
        return None

    async def drain(self, timeout: float = 0.3) -> list:
        messages = []
        try:
            while True:
                msg = await asyncio.wait_for(self.received.get(), timeout=timeout)
                messages.append(msg)
        except asyncio.TimeoutError:
            pass
        return messages

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1 — Prima connessione senza stato preesistente
# ══════════════════════════════════════════════════════════════════════════════


async def test_no_state_on_first_connect(base_url: str, timeout: float) -> bool:
    """
    Un client che si connette a una stanza vuota (senza stato pregresso)
    NON deve ricevere un game_state_sync inatteso.
    """
    step("Test 1 — Prima connessione: nessuno stato preesistente")

    room_id = f"test_fresh_{uuid.uuid4().hex[:8]}"
    room_url = f"{base_url}/{room_id}"

    client = WSClient("fresh", room_url)
    await client.connect()
    await asyncio.sleep(0.5)

    messages = await client.drain(timeout=0.5)
    state_syncs = [m for m in messages if m.get("event_type") == "game_state_sync"]

    if not state_syncs:
        ok("Nessun game_state_sync ricevuto su stanza vuota — corretto")
        passed = True
    else:
        fail(f"Ricevuto game_state_sync inatteso su stanza vuota: {state_syncs[0]}")
        passed = False

    await client.close()
    return passed


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2 — Riconnessione: riceve stato aggiornato
# ══════════════════════════════════════════════════════════════════════════════


async def test_reconnect_receives_state(base_url: str, timeout: float) -> bool:
    """
    Scenario:
      1. client_A si connette e invia un player_action con uno stato
      2. client_A si disconnette
      3. client_A si riconnette
      4. client_A deve ricevere game_state_sync con lo stato del punto 1
    """
    step("Test 2 — Riconnessione: riceve stato aggiornato")

    room_id = f"test_recon_{uuid.uuid4().hex[:8]}"
    room_url = f"{base_url}/{room_id}"
    state_value = f"state_{uuid.uuid4().hex[:8]}"

    # Connessione iniziale
    client_a = WSClient("recon_A", room_url)
    await client_a.connect()
    await asyncio.sleep(0.5)
    await client_a.drain(timeout=0.3)

    # Invia un evento che aggiorna lo stato
    await client_a.send(
        "player_action",
        {
            "test_state": state_value,
            "score": 42,
        },
    )
    await asyncio.sleep(0.5)

    # Disconnessione
    info("Disconnessione client_A...")
    await client_a.close()
    await asyncio.sleep(0.8)

    # Riconnessione con lo stesso client_id
    info("Riconnessione client_A...")
    client_a2 = WSClient("recon_A2", room_url, client_id=client_a.client_id)
    await client_a2.connect()

    # Deve ricevere game_state_sync entro il timeout
    sync_msg = await client_a2.wait_for("game_state_sync", timeout=timeout)

    passed = False
    if sync_msg is None:
        fail(f"Timeout: nessun game_state_sync ricevuto dopo la riconnessione")
        info(
            "Verifica che il backend abbia GameStateStore abilitato (core/state_store.py)"
        )
    else:
        state = sync_msg.get("payload", {}).get("state", {})
        if state.get("test_state") == state_value:
            ok(f"game_state_sync ricevuto con stato corretto")
            ok(f"  test_state={state.get('test_state')} score={state.get('score')}")
            passed = True
        else:
            fail(f"game_state_sync ricevuto ma stato non corrisponde")
            info(f"  Atteso test_state={state_value}")
            info(f"  Ricevuto: {state}")

    await client_a2.close()
    return passed


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3 — Riconnessione su istanza diversa
# ══════════════════════════════════════════════════════════════════════════════


async def test_reconnect_different_instance(
    url_a: str, url_b: str, timeout: float
) -> bool:
    """
    Scenario:
      1. client si connette al pod A e invia un player_action
      2. client si disconnette dal pod A
      3. client si riconnette al pod B (istanza diversa)
      4. client deve ricevere game_state_sync con lo stato salvato
         (letto da Redis, non dalla memoria locale del pod)
    """
    step("Test 3 — Riconnessione su istanza diversa (A → B)")

    room_id = f"test_cross_{uuid.uuid4().hex[:8]}"
    state_value = f"cross_{uuid.uuid4().hex[:8]}"

    # Connessione al pod A
    client = WSClient("cross_client", f"{url_a}/{room_id}")
    await client.connect()
    await asyncio.sleep(0.5)
    await client.drain(timeout=0.3)

    # Aggiorna lo stato dal pod A
    await client.send(
        "player_action",
        {
            "test_state": state_value,
            "origin_pod": "A",
        },
    )
    await asyncio.sleep(0.8)  # lascia tempo a Redis di salvare lo stato

    info(f"Stato aggiornato su pod A: test_state={state_value}")

    # Disconnessione da pod A
    await client.close()
    await asyncio.sleep(0.5)

    # Riconnessione al pod B (stesso client_id, istanza diversa)
    client_b = WSClient(
        "cross_client_B", f"{url_b}/{room_id}", client_id=client.client_id
    )
    await client_b.connect()

    # Deve ricevere game_state_sync dal pod B (stato letto da Redis)
    sync_msg = await client_b.wait_for("game_state_sync", timeout=timeout)

    passed = False
    if sync_msg is None:
        fail(f"Timeout: nessun game_state_sync dal pod B")
        info("Lo stato non è stato trasferito tra le istanze via Redis")
    else:
        state = sync_msg.get("payload", {}).get("state", {})
        instance_id = sync_msg.get("instance_id", "?")
        if state.get("test_state") == state_value:
            ok(f"game_state_sync ricevuto dal pod B (instance_id={instance_id})")
            ok(f"Stato corretto: test_state={state.get('test_state')}")
            passed = True
        else:
            fail(f"Stato non corrisponde sul pod B")
            info(f"  Atteso: test_state={state_value}")
            info(f"  Ricevuto: {state}")

    await client_b.close()
    return passed


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4 — Consistenza stato tra istanze
# ══════════════════════════════════════════════════════════════════════════════


async def test_state_consistency(base_url: str, timeout: float) -> bool:
    """
    Scenario:
      1. client_A e client_B si connettono alla stessa stanza
      2. client_A invia 3 player_action consecutivi
      3. Entrambi si disconnettono e si riconnettono
      4. Entrambi devono ricevere lo stesso stato (l'ultimo aggiornamento)
    """
    step("Test 4 — Consistenza stato tra client alla riconnessione")

    room_id = f"test_cons_{uuid.uuid4().hex[:8]}"
    room_url = f"{base_url}/{room_id}"

    client_a = WSClient("cons_A", room_url)
    client_b = WSClient("cons_B", room_url)
    await client_a.connect()
    await asyncio.sleep(0.2)
    await client_b.connect()
    await asyncio.sleep(0.5)
    await client_a.drain(timeout=0.3)
    await client_b.drain(timeout=0.3)

    # Invia 3 aggiornamenti di stato consecutivi
    final_value = None
    for i in range(3):
        final_value = f"v{i}_{uuid.uuid4().hex[:6]}"
        await client_a.send(
            "player_action",
            {
                "step": i,
                "test_state": final_value,
            },
        )
        await asyncio.sleep(0.2)

    info(f"Inviati 3 aggiornamenti, ultimo stato: {final_value}")
    await asyncio.sleep(0.5)

    # Disconnessione di entrambi
    await client_a.close()
    await client_b.close()
    await asyncio.sleep(0.8)

    # Riconnessione
    ra = WSClient("cons_A_recon", room_url, client_id=client_a.client_id)
    rb = WSClient("cons_B_recon", room_url, client_id=client_b.client_id)
    await ra.connect()
    await asyncio.sleep(0.2)
    await rb.connect()

    # Entrambi devono ricevere game_state_sync con lo stesso stato finale
    sync_a, sync_b = await asyncio.gather(
        ra.wait_for("game_state_sync", timeout=timeout),
        rb.wait_for("game_state_sync", timeout=timeout),
    )

    passed = True

    for name, sync in [("client_A", sync_a), ("client_B", sync_b)]:
        if sync is None:
            fail(f"{name}: nessun game_state_sync ricevuto")
            passed = False
        else:
            state = sync.get("payload", {}).get("state", {})
            if state.get("test_state") == final_value:
                ok(f"{name}: stato corretto (test_state={state.get('test_state')})")
            else:
                fail(f"{name}: stato non corrisponde")
                info(f"  Atteso: {final_value}")
                info(f"  Ricevuto: {state.get('test_state')}")
                passed = False

    await ra.close()
    await rb.close()
    return passed


# ══════════════════════════════════════════════════════════════════════════════
# TEST 5 — Registro player aggiornato dopo disconnessione
# ══════════════════════════════════════════════════════════════════════════════


async def test_player_registry(base_url: str, timeout: float) -> bool:
    """
    Verifica che la lista dei player nel payload di player_joined/player_left
    sia consistente dopo disconnessioni e riconnessioni.
    """
    step("Test 5 — Registro player consistente")

    room_id = f"test_players_{uuid.uuid4().hex[:8]}"
    room_url = f"{base_url}/{room_id}"

    # Connetti 3 client
    clients = [WSClient(f"p{i}", room_url) for i in range(3)]
    for c in clients:
        await c.connect()
        await asyncio.sleep(0.2)

    await asyncio.sleep(0.5)
    for c in clients:
        await c.drain(timeout=0.3)

    # Disconnetti il client 1 e aspetta il player_left
    info("Disconnessione client p1...")
    await clients[1].close()

    # p0 deve ricevere player_left con la lista aggiornata
    leave_msg = await clients[0].wait_for("player_left", timeout=timeout)

    passed = True
    if leave_msg is None:
        fail("Timeout: player_left non ricevuto")
        passed = False
    else:
        players_after = leave_msg.get("payload", {}).get("players", [])
        disconnected = clients[1].client_id
        if disconnected not in players_after:
            ok(f"Lista player aggiornata dopo disconnessione")
            ok(f"  Player rimosso: {disconnected[:16]}...")
            ok(f"  Player rimanenti: {len(players_after)}")
        else:
            fail(f"Il player disconnesso è ancora nella lista: {players_after}")
            passed = False

    for c in [clients[0], clients[2]]:
        await c.close()
    return passed


# ══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ══════════════════════════════════════════════════════════════════════════════


def print_summary(results: dict, mode: str) -> None:
    labels = {
        "no_state_first_connect": "Nessuno stato su prima connessione",
        "reconnect_state": "Stato ricevuto alla riconnessione",
        "reconnect_diff_instance": "Riconnessione su istanza diversa",
        "state_consistency": "Consistenza stato tra client",
        "player_registry": "Registro player dopo disconnessione",
    }
    print(f"\n{'='*55}")
    print(f"  Riepilogo Task 3.2 ({mode})")
    print(f"{'='*55}")
    all_passed = True
    for key, label in labels.items():
        if key not in results:
            continue
        if results[key]:
            print(f"  {GRN}✓{NC} {label}")
        else:
            print(f"  {RED}✗{NC} {label}")
            all_passed = False
    print(f"{'='*55}\n")
    sys.exit(0 if all_passed else 1)


async def run_ingress_tests(base_url: str, timeout: float) -> None:
    print(
        f"\n{'='*55}\n  Task 3.2 — Test Riconnessione (via Ingress)\n  URL: {base_url}\n{'='*55}"
    )
    results = {}
    for key, coro in [
        ("no_state_first_connect", test_no_state_on_first_connect(base_url, timeout)),
        ("reconnect_state", test_reconnect_receives_state(base_url, timeout)),
        ("state_consistency", test_state_consistency(base_url, timeout)),
        ("player_registry", test_player_registry(base_url, timeout)),
    ]:
        try:
            results[key] = await coro
        except Exception as e:
            fail(f"Errore imprevisto: {e}")
            results[key] = False

    info("Test 3 (riconnessione cross-istanza) richiede --pod-a e --pod-b")
    print_summary(results, "via Ingress")


async def run_direct_tests(url_a: str, url_b: str, timeout: float) -> None:
    base_url = url_a  # usa pod A come base per i test non cross-istanza
    print(
        f"\n{'='*55}\n  Task 3.2 — Test Riconnessione (modalità diretta)\n  Pod A: {url_a}\n  Pod B: {url_b}\n{'='*55}"
    )
    results = {}
    for key, coro in [
        ("no_state_first_connect", test_no_state_on_first_connect(base_url, timeout)),
        ("reconnect_state", test_reconnect_receives_state(base_url, timeout)),
        (
            "reconnect_diff_instance",
            test_reconnect_different_instance(url_a, url_b, timeout),
        ),
        ("state_consistency", test_state_consistency(base_url, timeout)),
        ("player_registry", test_player_registry(base_url, timeout)),
    ]:
        try:
            results[key] = await coro
        except Exception as e:
            fail(f"Errore imprevisto: {e}")
            results[key] = False

    print_summary(results, "modalità diretta")


def main():
    parser = argparse.ArgumentParser(description="Test riconnessione client - Task 3.2")
    parser.add_argument("--url", default="ws://game.local/ws")
    parser.add_argument("--pod-a", default=None)
    parser.add_argument("--pod-b", default=None)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    if args.pod_a and args.pod_b:
        asyncio.run(run_direct_tests(args.pod_a, args.pod_b, args.timeout))
    else:
        asyncio.run(run_ingress_tests(args.url, args.timeout))


if __name__ == "__main__":
    main()
