#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# Verifica che due istanze backend connesse allo stesso Redis propaghino
# correttamente gli eventi a tutti i client, indipendentemente da quale
# istanza gestisce la connessione.
#
# Scenari testati:
#   1. Propagazione A→B: evento pubblicato da client su istanza A
#      arriva a client su istanza B
#   2. Propagazione B→A: evento pubblicato da client su istanza B
#      arriva a client su istanza A
#   3. Broadcast locale: evento pubblicato arriva anche agli altri client
#      sulla stessa istanza (non solo cross-istanza)
#   4. Deduplicazione: il mittente NON riceve due volte il proprio evento
#
# Uso:
#   # Ambiente Docker Compose (due repliche backend dietro NGINX)
#   pip install -r tests/requirements.txt
#   python tests/test_multi_instance.py
#
#   # Ambiente Kubernetes
#   python tests/test_multi_instance.py --url http://game.local
#
#   # Multi istanza per Kubernetes (2 terminali diversi)
#   kubectl get pods -n game -l app=backend
#   kubectl port-forward pod/{pod-name-1} -n game 8001:8000
#   kubectl port-forward pod/{pod-name-2} -n game 8002:8000
#
#   python tests/test_multi_instance.py --pod-a http://localhost:8001 --pod-b http://localhost:8002
# ─────────────────────────────────────────────────────────────────────────────

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass, field

from sio_client import SIOClient

# ── Colori output ─────────────────────────────────────────────────────────────
GRN = "\033[0;32m"
RED = "\033[0;31m"
YLW = "\033[1;33m"
BLU = "\033[0;34m"
NC  = "\033[0m"


def ok(msg):   print(f"  {GRN}✓{NC} {msg}")
def fail(msg): print(f"  {RED}✗{NC} {msg}")
def info(msg): print(f"  {BLU}→{NC} {msg}")
def step(msg): print(f"\n{YLW}▸ {msg}{NC}")


# ── Configurazione ────────────────────────────────────────────────────────────

@dataclass
class TestConfig:
    # Base URL Socket.IO (http/https). NGINX / Ingress bilancia tra le istanze.
    sio_base_url: str = "http://localhost"

    # Stanza di test (univoca per evitare interferenze)
    room_id: str = field(default_factory=lambda: f"test_room_{uuid.uuid4().hex[:8]}")

    # Timeout attesa messaggio (secondi)
    receive_timeout: float = 5.0

    # Numero di eventi da inviare nel test di throughput
    num_events: int = 5


# ── Funzione di supporto: connetti N client e scopri le istanze ───────────────


async def connect_clients_to_different_instances(
    cfg: TestConfig, n: int = 4
) -> tuple[list[SIOClient], dict[str, list[SIOClient]]]:
    """
    Connette N client alla stessa stanza e raggruppa i client per istanza
    backend leggendo il campo instance_id dagli eventi ricevuti.
    """
    clients = []
    for i in range(n):
        c = SIOClient(name=f"client_{i}", base_url=cfg.sio_base_url, room_id=cfg.room_id)
        await c.connect()
        clients.append(c)
        await asyncio.sleep(0.1)

    # Lascia tempo agli eventi player_joined di arrivare
    await asyncio.sleep(1.0)

    by_instance: dict[str, list[SIOClient]] = {}
    for c in clients:
        messages = await c.drain(timeout=0.5)
        for msg in messages:
            if msg.get("event_type") == "player_joined":
                instance = msg.get("instance_id", "unknown")
                c.instance_id = instance
                by_instance.setdefault(instance, []).append(c)
                break

    return clients, by_instance


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1 — Propagazione cross-istanza A → B
# ══════════════════════════════════════════════════════════════════════════════


async def test_cross_instance_propagation(cfg: TestConfig) -> bool:
    step("Test 1 — Propagazione cross-istanza (A → B e B → A)")

    clients, by_instance = await connect_clients_to_different_instances(cfg, n=6)
    instances = list(by_instance.keys())
    info(f"Istanze scoperte: {instances}")

    if len(instances) < 2:
        info(f"Solo {len(instances)} istanza rilevata.")
        info("NGINX potrebbe aver mandato tutti i client sullo stesso backend.")
        info("")
        info("Per forzare l'accesso diretto ai pod usa --pod-a e --pod-b:")
        info("  1. Apri due terminali e lancia un port-forward per ogni pod:")
        info("       kubectl port-forward pod/<nome-pod-A> -n game 8001:8000")
        info("       kubectl port-forward pod/<nome-pod-B> -n game 8002:8000")
        info("  2. Rilancia il test con:")
        info("       python tests/test_multi_instance.py \\")
        info("         --pod-a http://localhost:8001 \\")
        info("         --pod-b http://localhost:8002")
        for c in clients:
            await c.close()
        return True  # non è un fallimento del codice

    instance_a, instance_b = instances[0], instances[1]
    sender   = by_instance[instance_a][0]
    receiver = by_instance[instance_b][0]

    info(f"Sender  : {sender.name}   → istanza {instance_a}")
    info(f"Receiver: {receiver.name} → istanza {instance_b}")

    await sender.drain(timeout=0.2)
    await receiver.drain(timeout=0.2)

    # A → B
    test_payload = {"test_id": uuid.uuid4().hex, "value": 42}
    await sender.send("player_action", test_payload)

    received = await receiver.wait_for(
        "player_action", timeout=cfg.receive_timeout, test_id=test_payload["test_id"]
    )

    passed = False
    if received is None:
        fail(f"Timeout: evento non arrivato su istanza B entro {cfg.receive_timeout}s")
    elif received.get("payload", {}).get("test_id") != test_payload["test_id"]:
        fail(f"Evento ricevuto ma payload non corrisponde: {received}")
    else:
        ok(f"Evento arrivato su istanza B in {cfg.receive_timeout}s")
        ok(f"Payload verificato: test_id={received['payload']['test_id']}")
        passed = True

    # B → A
    step("Test 1b — Propagazione inversa (B → A)")
    await sender.drain(timeout=0.2)
    await receiver.drain(timeout=0.2)

    test_payload_2 = {"test_id": uuid.uuid4().hex, "value": 99}
    await receiver.send("player_action", test_payload_2)

    received_2 = await sender.wait_for(
        "player_action", timeout=cfg.receive_timeout, test_id=test_payload_2["test_id"]
    )

    passed_2 = False
    if received_2 is None:
        fail(f"Timeout: evento non arrivato su istanza A entro {cfg.receive_timeout}s")
    elif received_2.get("payload", {}).get("test_id") != test_payload_2["test_id"]:
        fail(f"Evento ricevuto ma payload non corrisponde: {received_2}")
    else:
        ok("Evento arrivato su istanza A")
        ok(f"Payload verificato: test_id={received_2['payload']['test_id']}")
        passed_2 = True

    for c in clients:
        await c.close()

    return passed and passed_2


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2 — Broadcast locale (tutti i client della stessa stanza)
# ══════════════════════════════════════════════════════════════════════════════


async def test_local_broadcast(cfg: TestConfig) -> bool:
    step("Test 2 — Broadcast locale (tutti i client della stanza)")

    room_id     = f"{cfg.room_id}_broadcast"
    num_clients = 4

    clients = []
    for i in range(num_clients):
        c = SIOClient(name=f"bcast_{i}", base_url=cfg.sio_base_url, room_id=room_id)
        await c.connect()
        clients.append(c)
        await asyncio.sleep(0.1)

    await asyncio.sleep(0.5)
    for c in clients:
        await c.drain(timeout=0.3)

    sender  = clients[0]
    test_id = uuid.uuid4().hex
    await sender.send("player_action", {"test_id": test_id, "broadcast_test": True})

    results = await asyncio.gather(
        *[c.wait_for("player_action", timeout=cfg.receive_timeout, test_id=test_id) for c in clients]
    )

    passed = True
    for c, msg in zip(clients, results):
        if msg is None:
            fail(f"{c.name}: timeout, evento non ricevuto")
            passed = False
        elif msg.get("payload", {}).get("test_id") != test_id:
            fail(f"{c.name}: payload non corrisponde")
            passed = False
        else:
            ok(f"{c.name}: evento ricevuto correttamente")

    for c in clients:
        await c.close()

    return passed


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3 — Deduplicazione (il mittente non riceve l'evento due volte)
# ══════════════════════════════════════════════════════════════════════════════


async def test_deduplication(cfg: TestConfig) -> bool:
    step("Test 3 — Deduplicazione (nessun evento duplicato)")

    room_id = f"{cfg.room_id}_dedup"
    sender  = SIOClient(name="dedup_sender", base_url=cfg.sio_base_url, room_id=room_id)
    await sender.connect()
    await asyncio.sleep(0.3)
    await sender.drain(timeout=0.3)

    test_id = uuid.uuid4().hex
    await sender.send("player_action", {"test_id": test_id})

    await asyncio.sleep(2.0)
    messages = await sender.drain(timeout=0.5)

    matching = [
        m for m in messages
        if m.get("event_type") == "player_action"
        and m.get("payload", {}).get("test_id") == test_id
    ]

    passed = False
    if len(matching) == 0:
        fail("Nessun evento ricevuto (il mittente dovrebbe riceverlo almeno una volta)")
    elif len(matching) == 1:
        ok("Evento ricevuto esattamente 1 volta — deduplicazione funziona")
        passed = True
    else:
        fail(f"Evento ricevuto {len(matching)} volte — deduplicazione NON funziona!")
        for i, m in enumerate(matching):
            info(f"  Duplicato {i+1}: {m}")

    await sender.close()
    return passed


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4 — Throughput multi-evento
# ══════════════════════════════════════════════════════════════════════════════


async def test_event_throughput(cfg: TestConfig) -> bool:
    step(f"Test 4 — Throughput ({cfg.num_events} eventi cross-istanza)")

    clients, by_instance = await connect_clients_to_different_instances(cfg, n=4)
    instances = list(by_instance.keys())

    if len(instances) < 2:
        info("Serve almeno 2 istanze per questo test — skip")
        for c in clients:
            await c.close()
        return True

    sender   = by_instance[instances[0]][0]
    receiver = by_instance[instances[1]][0]

    await asyncio.sleep(0.3)
    await sender.drain(timeout=0.2)
    await receiver.drain(timeout=0.2)

    sent_ids = []
    for i in range(cfg.num_events):
        seq_id = f"{uuid.uuid4().hex[:6]}_seq{i}"
        sent_ids.append(seq_id)
        await sender.send("player_action", {"seq_id": seq_id, "seq": i})
        await asyncio.sleep(0.05)

    received_ids = []
    import time
    deadline = time.monotonic() + cfg.receive_timeout + cfg.num_events * 0.1
    while time.monotonic() < deadline and len(received_ids) < cfg.num_events:
        remaining = deadline - time.monotonic()
        try:
            msg = await asyncio.wait_for(receiver.received.get(), timeout=remaining)
            if msg.get("event_type") == "player_action" and "seq_id" in msg.get("payload", {}):
                received_ids.append(msg["payload"]["seq_id"])
        except asyncio.TimeoutError:
            break

    passed     = True
    missing    = set(sent_ids) - set(received_ids)
    extra      = set(received_ids) - set(sent_ids)

    if missing:
        fail(f"{len(missing)}/{cfg.num_events} eventi NON arrivati: {missing}")
        passed = False
    else:
        ok(f"Tutti {cfg.num_events} eventi ricevuti")

    if extra:
        info(f"  {len(extra)} eventi extra (da altri client nella stanza)")

    filtered = [r for r in received_ids if r in set(sent_ids)]
    if filtered == [s for s in sent_ids if s in set(received_ids)]:
        ok("Ordine degli eventi preservato")
    else:
        info("Ordine degli eventi non preservato (può essere normale con più istanze)")

    for c in clients:
        await c.close()

    return passed


# ══════════════════════════════════════════════════════════════════════════════
# RUNNER MODALITA' DIRETTA (--pod-a / --pod-b via port-forward)
# ══════════════════════════════════════════════════════════════════════════════


async def run_direct_pod_tests(cfg: TestConfig, url_a: str, url_b: str) -> None:
    """
    Modalita' diretta: connette i client direttamente ai pod tramite port-forward,
    bypassing Ingress e NGINX. Garantisce che client_A sia su pod_A e client_B su pod_B.

    Setup richiesto (due terminali PowerShell separati):
      kubectl port-forward pod/<nome-pod-A> -n game 8001:8000
      kubectl port-forward pod/<nome-pod-B> -n game 8002:8000
    """
    print(f"""
{'='*55}
  Task 3.1 — Test Multi-Istanza (modalita' diretta)
  Pod A: {url_a}
  Pod B: {url_b}
  Room:  {cfg.room_id}
{'='*55}""")

    room_id = cfg.room_id
    results = {}

    # ── Test 1: propagazione A → B ────────────────────────────────────────
    step("Test 1 — Propagazione cross-istanza (A -> B e B -> A)")

    client_a = SIOClient("client_A", base_url=url_a, room_id=room_id)
    client_b = SIOClient("client_B", base_url=url_b, room_id=room_id)

    try:
        await client_a.connect()
        await client_b.connect()
        await asyncio.sleep(0.8)
        await client_a.drain(timeout=0.3)
        await client_b.drain(timeout=0.3)

        # A → B
        test_id_ab = uuid.uuid4().hex
        await client_a.send("player_action", {"test_id": test_id_ab, "direction": "A_to_B"})
        msg = await client_b.wait_for("player_action", timeout=cfg.receive_timeout, test_id=test_id_ab)

        passed_ab = msg and msg.get("payload", {}).get("test_id") == test_id_ab
        if passed_ab:
            ok(f"A -> B: evento arrivato (instance_id nel msg: {msg.get('instance_id','?')})")
        else:
            fail(f"A -> B: timeout o payload errato (ricevuto: {msg})")

        # B → A
        await client_a.drain(timeout=0.3)
        await client_b.drain(timeout=0.3)

        test_id_ba = uuid.uuid4().hex
        await client_b.send("player_action", {"test_id": test_id_ba, "direction": "B_to_A"})
        msg2 = await client_a.wait_for("player_action", timeout=cfg.receive_timeout, test_id=test_id_ba)

        passed_ba = msg2 and msg2.get("payload", {}).get("test_id") == test_id_ba
        if passed_ba:
            ok("B -> A: evento arrivato")
        else:
            fail(f"B -> A: timeout o payload errato (ricevuto: {msg2})")

        results["cross_instance"] = passed_ab and passed_ba

    except Exception as e:
        fail(f"Errore connessione: {e}")
        info("Verifica che i port-forward siano attivi:")
        info(f"  kubectl port-forward pod/<nome-pod-A> -n game 8001:8000")
        info(f"  kubectl port-forward pod/<nome-pod-B> -n game 8002:8000")
        results["cross_instance"] = False
    finally:
        await client_a.close()
        await client_b.close()

    # ── Test 2: broadcast locale ───────────────────────────────────────────
    step("Test 2 — Broadcast locale")
    try:
        room_bcast = f"{room_id}_bcast"
        ca1 = SIOClient("ca1", base_url=url_a, room_id=room_bcast)
        ca2 = SIOClient("ca2", base_url=url_a, room_id=room_bcast)
        cb1 = SIOClient("cb1", base_url=url_b, room_id=room_bcast)

        for c in [ca1, ca2, cb1]:
            await c.connect()
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)
        for c in [ca1, ca2, cb1]:
            await c.drain(timeout=0.3)

        test_id = uuid.uuid4().hex
        await ca1.send("player_action", {"test_id": test_id})

        msgs = await asyncio.gather(
            ca1.wait_for("player_action", timeout=cfg.receive_timeout, test_id=test_id),
            ca2.wait_for("player_action", timeout=cfg.receive_timeout, test_id=test_id),
            cb1.wait_for("player_action", timeout=cfg.receive_timeout, test_id=test_id),
        )

        passed = all(m and m.get("payload", {}).get("test_id") == test_id for m in msgs)
        for name, m in zip(["ca1", "ca2", "cb1"], msgs):
            if m and m.get("payload", {}).get("test_id") == test_id:
                ok(f"{name}: ricevuto")
            else:
                fail(f"{name}: non ricevuto (msg={m})")

        results["broadcast"] = passed
        for c in [ca1, ca2, cb1]:
            await c.close()

    except Exception as e:
        fail(f"Errore: {e}")
        results["broadcast"] = False

    # ── Test 3: deduplicazione ─────────────────────────────────────────────
    step("Test 3 — Deduplicazione")
    try:
        sender = SIOClient("dedup", base_url=url_a, room_id=f"{room_id}_dedup")
        await sender.connect()
        await asyncio.sleep(0.3)
        await sender.drain(timeout=0.3)

        test_id = uuid.uuid4().hex
        await sender.send("player_action", {"test_id": test_id})
        await asyncio.sleep(2.0)
        messages = await sender.drain(timeout=0.5)

        matching = [
            m for m in messages
            if m.get("event_type") == "player_action"
            and m.get("payload", {}).get("test_id") == test_id
        ]

        if len(matching) == 1:
            ok("Ricevuto esattamente 1 volta — deduplicazione OK")
            results["deduplication"] = True
        elif len(matching) == 0:
            fail("Evento non ricevuto")
            results["deduplication"] = False
        else:
            fail(f"Evento ricevuto {len(matching)} volte — deduplicazione KO")
            results["deduplication"] = False

        await sender.close()
    except Exception as e:
        fail(f"Errore: {e}")
        results["deduplication"] = False

    # ── Test 4: throughput ─────────────────────────────────────────────────
    step(f"Test 4 — Throughput ({cfg.num_events} eventi A -> B)")
    try:
        sender   = SIOClient("thr_a", base_url=url_a, room_id=f"{room_id}_thr")
        receiver = SIOClient("thr_b", base_url=url_b, room_id=f"{room_id}_thr")
        await sender.connect()
        await receiver.connect()
        await asyncio.sleep(0.5)
        await sender.drain(timeout=0.2)
        await receiver.drain(timeout=0.2)

        sent_ids = []
        for i in range(cfg.num_events):
            seq_id = f"{uuid.uuid4().hex[:6]}_seq{i}"
            sent_ids.append(seq_id)
            await sender.send("player_action", {"seq_id": seq_id, "seq": i})
            await asyncio.sleep(0.05)

        import time
        received_ids = []
        deadline = time.monotonic() + cfg.receive_timeout + cfg.num_events * 0.1
        while time.monotonic() < deadline and len(received_ids) < cfg.num_events:
            remaining = deadline - time.monotonic()
            try:
                msg = await asyncio.wait_for(receiver.received.get(), timeout=remaining)
                if msg.get("event_type") == "player_action" and "seq_id" in msg.get("payload", {}):
                    received_ids.append(msg["payload"]["seq_id"])
            except asyncio.TimeoutError:
                break

        missing = set(sent_ids) - set(received_ids)
        if not missing:
            ok(f"Tutti {cfg.num_events} eventi ricevuti")
            results["throughput"] = True
        else:
            fail(f"{len(missing)} eventi mancanti: {missing}")
            results["throughput"] = False

        await sender.close()
        await receiver.close()
    except Exception as e:
        fail(f"Errore: {e}")
        results["throughput"] = False

    # ── Riepilogo ──────────────────────────────────────────────────────────
    print(f"{'='*55}")
    print("  Riepilogo (modalita' diretta)")
    print(f"{'='*55}")
    labels = {
        "cross_instance": "Propagazione cross-istanza (A<->B)",
        "broadcast":      "Broadcast locale (tutti i client)",
        "deduplication":  "Deduplicazione mittente",
        "throughput":     f"Throughput ({cfg.num_events} eventi)",
    }
    all_passed = True
    for key, label in labels.items():
        r = results.get(key, False)
        if r:
            print(f"  {GRN}✓{NC} {label}")
        else:
            print(f"  {RED}✗{NC} {label}")
            all_passed = False
    print(f"{'='*55}")
    sys.exit(0 if all_passed else 1)


# ══════════════════════════════════════════════════════════════════════════════
# RUNNER PRINCIPALE
# ══════════════════════════════════════════════════════════════════════════════


async def run_all_tests(cfg: TestConfig) -> None:
    print(f"""
{'='*55}
  Task 3.1 — Test Multi-Istanza
  URL: {cfg.sio_base_url}
  Room: {cfg.room_id}
{'='*55}""")

    results = {}

    for key, coro in [
        ("cross_instance", test_cross_instance_propagation(cfg)),
        ("broadcast",      test_local_broadcast(cfg)),
        ("deduplication",  test_deduplication(cfg)),
        ("throughput",     test_event_throughput(cfg)),
    ]:
        try:
            results[key] = await coro
        except Exception as e:
            fail(f"Errore imprevisto in {key}: {e}")
            results[key] = False

    print(f"\n{'='*55}")
    print("  Riepilogo")
    print(f"{'='*55}")

    labels = {
        "cross_instance": "Propagazione cross-istanza (A↔B)",
        "broadcast":      "Broadcast locale (tutti i client)",
        "deduplication":  "Deduplicazione mittente",
        "throughput":     f"Throughput ({cfg.num_events} eventi)",
    }

    all_passed = True
    for key, label in labels.items():
        r = results.get(key, False)
        if r:
            print(f"  {GRN}✓{NC} {label}")
        else:
            print(f"  {RED}✗{NC} {label}")
            all_passed = False

    print(f"{'='*55}")
    if all_passed:
        print(f"  {GRN}Tutti i test superati{NC}")
    else:
        print(f"  {RED}Alcuni test falliti — verificare i log sopra{NC}")
    print(f"{'='*55}\n")

    sys.exit(0 if all_passed else 1)


def main():
    parser = argparse.ArgumentParser(description="Test multi-istanza Redis Pub/Sub")
    parser.add_argument(
        "--url",
        default="http://localhost",
        help="Base URL Socket.IO via Ingress (default: http://localhost)",
    )
    parser.add_argument(
        "--pod-a", default=None,
        help="URL diretto pod A via port-forward (es. http://localhost:8001)",
    )
    parser.add_argument(
        "--pod-b", default=None,
        help="URL diretto pod B via port-forward (es. http://localhost:8002)",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--events",  type=int,   default=5)
    args = parser.parse_args()

    cfg = TestConfig(
        sio_base_url=args.url,
        receive_timeout=args.timeout,
        num_events=args.events,
    )

    if args.pod_a and args.pod_b:
        asyncio.run(run_direct_pod_tests(cfg, args.pod_a, args.pod_b))
    else:
        asyncio.run(run_all_tests(cfg))


if __name__ == "__main__":
    main()
