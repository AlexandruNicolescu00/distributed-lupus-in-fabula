#!/usr/bin/env python3
# tests/load_test.py
# ─────────────────────────────────────────────────────────────────────────────
# Task 4.1 — Test di carico
#
# Simula N client WebSocket connessi contemporaneamente su più stanze,
# misura latenza, throughput e stabilità, identifica bottleneck nel
# layer Redis Pub/Sub e nel proxy.
#
# Metriche raccolte:
#   - Latenza round-trip (RTT): tempo tra SEND e ricezione dell'echo
#   - Throughput: messaggi/sec inviati e ricevuti globalmente
#   - Tasso di errori: connessioni fallite, messaggi persi, timeout
#   - Stabilità: disconnessioni inattese durante il test
#   - Fan-out: tempo perché un evento raggiunga tutti i client di una stanza
#
# Uso:
#   pip install websockets
#
#   # Test base: 50 client, 5 stanze, 30 secondi
#   python tests/load_test.py --url ws://game.local/ws
#
#   # Test intensivo: 200 client, 10 stanze, 60 secondi
#   python tests/load_test.py --url ws://game.local/ws \
#     --clients 200 --rooms 10 --duration 60
#
#   # Solo connessioni (misura connection rate senza invio messaggi)
#   python tests/load_test.py --url ws://game.local/ws --mode connect-only
# ─────────────────────────────────────────────────────────────────────────────

import argparse
import asyncio
import json
import math
import sys
import time
import uuid
from dataclasses import dataclass, field
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
CYN = "\033[0;36m"
NC = "\033[0m"


def ok(msg):
    print(f"  {GRN}✓{NC} {msg}")


def fail(msg):
    print(f"  {RED}✗{NC} {msg}")


def info(msg):
    print(f"  {BLU}→{NC} {msg}")


def warn(msg):
    print(f"  {YLW}!{NC} {msg}")


def step(msg):
    print(f"\n{CYN}▸ {msg}{NC}")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURAZIONE
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class LoadTestConfig:
    ws_base_url: str = "ws://game.local/ws"
    num_clients: int = 50
    num_rooms: int = 5
    duration_s: float = 30.0
    send_interval: float = 0.5  # secondi tra messaggi per ogni client
    connect_delay: float = 0.05  # secondi tra una connessione e l'altra (ramp-up)
    mode: str = "full"  # full | connect-only | fanout
    timeout_conn: float = 10.0  # timeout connessione WebSocket


# ══════════════════════════════════════════════════════════════════════════════
# RACCOLTA METRICHE
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class Metrics:
    # Connessioni
    connections_attempted: int = 0
    connections_ok: int = 0
    connections_failed: int = 0
    disconnections_unexpected: int = 0

    # Messaggi
    messages_sent: int = 0
    messages_received: int = 0
    messages_lost: int = 0  # inviati ma non ricevuti entro timeout
    messages_ignored: int = 0  # player_action ricevuti ma msg_id non in my_pending

    # Latenza RTT (millisecondi)
    rtt_samples: list = field(default_factory=list)

    # Fan-out (millisecondi): tempo perché un evento raggiunga tutti i client
    fanout_samples: list = field(default_factory=list)

    # Errori
    errors: list = field(default_factory=list)

    # Timing
    start_time: float = 0.0
    end_time: float = 0.0

    def elapsed(self) -> float:
        return (self.end_time or time.monotonic()) - self.start_time

    def throughput_sent(self) -> float:
        e = self.elapsed()
        return self.messages_sent / e if e > 0 else 0

    def throughput_received(self) -> float:
        e = self.elapsed()
        return self.messages_received / e if e > 0 else 0

    def percentile(self, samples: list, p: float) -> float:
        if not samples:
            return 0.0
        s = sorted(samples)
        idx = int(math.ceil(p / 100.0 * len(s))) - 1
        return s[max(0, idx)]

    def rtt_p50(self) -> float:
        return self.percentile(self.rtt_samples, 50)

    def rtt_p95(self) -> float:
        return self.percentile(self.rtt_samples, 95)

    def rtt_p99(self) -> float:
        return self.percentile(self.rtt_samples, 99)

    def rtt_mean(self) -> float:
        return (
            sum(self.rtt_samples) / len(self.rtt_samples) if self.rtt_samples else 0.0
        )

    def error_rate(self) -> float:
        total = self.messages_sent
        if total == 0:
            return 0.0
        return min(100.0, self.messages_lost / total * 100)

    def success_rate(self) -> float:
        a = self.connections_attempted
        return (self.connections_ok / a * 100) if a > 0 else 0.0


# Metriche globali condivise tra i worker (accesso protetto da Lock)
metrics = Metrics()
metrics_lock = asyncio.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# WORKER CLIENT
# ══════════════════════════════════════════════════════════════════════════════


async def client_worker(
    client_id: str,
    room_url: str,
    cfg: LoadTestConfig,
    stop_event: asyncio.Event,
) -> None:
    """
    Singolo client WebSocket che invia messaggi periodicamente e misura
    il round-trip time. Ogni client traccia i propri msg_id in un dict
    privato: conta solo l'eco dei messaggi che ha inviato lui stesso.
    """
    # Dict privato per questo client: msg_id → send_timestamp
    my_pending: dict = {}
    async with metrics_lock:
        metrics.connections_attempted += 1

    ws = None
    try:
        ws = await asyncio.wait_for(
            websockets.connect(
                f"{room_url}?client_id={client_id}",
                open_timeout=cfg.timeout_conn,
            ),
            timeout=cfg.timeout_conn,
        )
    except Exception as e:
        async with metrics_lock:
            metrics.connections_failed += 1
            metrics.errors.append(f"Connect fail [{client_id[:12]}]: {e}")
        return

    async with metrics_lock:
        metrics.connections_ok += 1

    # Task di ricezione
    async def receiver():
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("event_type") == "player_action":
                    msg_id = msg.get("payload", {}).get("msg_id")
                    if msg_id and msg_id in my_pending:
                        # Eco del proprio messaggio
                        send_ts = my_pending.pop(msg_id)
                        rtt_ms = (time.monotonic() - send_ts) * 1000
                        async with metrics_lock:
                            metrics.messages_received += 1
                            metrics.rtt_samples.append(rtt_ms)
                    elif msg_id:
                        # player_action ricevuto ma non nostro (da altri client)
                        async with metrics_lock:
                            metrics.messages_ignored += 1
        except websockets.ConnectionClosed:
            if not stop_event.is_set():
                async with metrics_lock:
                    metrics.disconnections_unexpected += 1

    recv_task = asyncio.create_task(receiver())

    # Loop di invio
    room_id = room_url.split("/ws/")[-1].split("?")[0]
    try:
        while not stop_event.is_set():
            msg_id = uuid.uuid4().hex
            send_ts = time.monotonic()

            payload = {
                "msg_id": msg_id,
                "client_id": client_id,
                "ts": send_ts,
            }
            try:
                # Registra PRIMA di inviare: se l'eco arriva quasi istantaneamente
                # (e asyncio schedula recv_task prima di tornare qui) troverebbe
                # my_pending vuoto e scarterebbe l'eco come "di altri client"
                my_pending[msg_id] = send_ts
                await ws.send(
                    json.dumps(
                        {
                            "event_type": "player_action",
                            "room_id": room_id,
                            "payload": payload,
                        }
                    )
                )
                async with metrics_lock:
                    metrics.messages_sent += 1
            except Exception as e:
                async with metrics_lock:
                    metrics.errors.append(f"Send fail [{client_id[:12]}]: {e}")

            await asyncio.sleep(cfg.send_interval)

        # Grace period: continua a ricevere echo in volo senza inviare.
        await asyncio.sleep(3.0)

    finally:
        # Chiudi il socket PRIMA di cancellare il recv_task:
        # la chiusura fa terminare naturalmente l'async-for nel receiver,
        # svuotando i messaggi già arrivati nel buffer prima di uscire.
        # Cancellarlo subito invece butta via quei messaggi.
        try:
            await ws.close()
        except:
            pass
        try:
            await asyncio.wait_for(recv_task, timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        finally:
            recv_task.cancel()

        # Non contare i persi qui: il dict è condiviso tra tutti i worker
        # della stanza, quindi ogni worker conterebbe le voci degli altri
        # (10x overcounting). Il conteggio viene fatto una volta sola
        # nel runner principale dopo asyncio.gather().
        pass


# ══════════════════════════════════════════════════════════════════════════════
# WORKER FANOUT
# ══════════════════════════════════════════════════════════════════════════════


async def fanout_worker(room_url: str, cfg: LoadTestConfig, n_clients: int = 5) -> None:
    """
    Misura il fan-out: tempo impiegato perché un evento pubblicato da
    un sender raggiunga tutti gli N-1 receiver della stessa stanza.
    """
    room_id = room_url.split("/ws/")[-1].split("?")[0]
    clients = []

    for i in range(n_clients):
        try:
            ws = await asyncio.wait_for(
                websockets.connect(
                    f"{room_url}?client_id=fanout_{i}_{uuid.uuid4().hex[:6]}"
                ),
                timeout=cfg.timeout_conn,
            )
            clients.append(ws)
        except Exception:
            pass

    if len(clients) < 2:
        return

    await asyncio.sleep(0.5)  # lascia arrivare i player_joined

    sender = clients[0]
    receivers = clients[1:]
    recv_queues = [asyncio.Queue() for _ in receivers]

    async def recv_loop(ws, q):
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("event_type") == "player_action":
                    await q.put(time.monotonic())
        except:
            pass

    tasks = [
        asyncio.create_task(recv_loop(ws, q)) for ws, q in zip(receivers, recv_queues)
    ]

    for _ in range(5):  # 5 misurazioni
        msg_id = uuid.uuid4().hex
        send_ts = time.monotonic()

        await sender.send(
            json.dumps(
                {
                    "event_type": "player_action",
                    "room_id": room_id,
                    "payload": {"msg_id": msg_id},
                }
            )
        )

        # Aspetta che tutti i receiver ricevano l'evento
        try:
            recv_times = await asyncio.wait_for(
                asyncio.gather(*[q.get() for q in recv_queues]),
                timeout=3.0,
            )
            last_recv = max(recv_times)
            fanout_ms = (last_recv - send_ts) * 1000
            async with metrics_lock:
                metrics.fanout_samples.append(fanout_ms)
        except asyncio.TimeoutError:
            async with metrics_lock:
                metrics.messages_lost += len(receivers)

        await asyncio.sleep(0.5)

    for t in tasks:
        t.cancel()
    for ws in clients:
        try:
            await ws.close()
        except:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# PROGRESS BAR
# ══════════════════════════════════════════════════════════════════════════════


async def progress_reporter(cfg: LoadTestConfig, stop_event: asyncio.Event) -> None:
    start = time.monotonic()
    while not stop_event.is_set():
        await asyncio.sleep(5)
        elapsed = time.monotonic() - start
        remaining = max(0, cfg.duration_s - elapsed)
        async with metrics_lock:
            conn = metrics.connections_ok
            sent = metrics.messages_sent
            recv = metrics.messages_received
            errors = metrics.connections_failed + metrics.disconnections_unexpected
            rtt_p = metrics.rtt_p95()
        print(
            f"  [{elapsed:5.0f}s / {cfg.duration_s:.0f}s]  "
            f"conn={conn}  sent={sent}  recv={recv}  "
            f"rtt_p95={rtt_p:.1f}ms  errors={errors}  "
            f"(rimanenti: {remaining:.0f}s)"
        )


# ══════════════════════════════════════════════════════════════════════════════
# RUNNER PRINCIPALE
# ══════════════════════════════════════════════════════════════════════════════


async def run_load_test(cfg: LoadTestConfig) -> None:
    print(
        f"""
{'='*60}
  Task 4.1 — Test di carico
  URL:     {cfg.ws_base_url}
  Client:  {cfg.num_clients}
  Stanze:  {cfg.num_rooms}
  Durata:  {cfg.duration_s}s
  Modo:    {cfg.mode}
  Invio:   ogni {cfg.send_interval}s per client
{'='*60}"""
    )

    metrics.start_time = time.monotonic()
    stop_event = asyncio.Event()

    # Distribuisci i client tra le stanze
    run_id = uuid.uuid4().hex[:8]
    room_ids = [f"load_{run_id}_room{i}" for i in range(cfg.num_rooms)]
    clients_per_room = cfg.num_clients // cfg.num_rooms

    # pending_rtts e rtt_locks rimossi: ogni worker usa il proprio dict privato

    step("Fase 1 — Ramp-up connessioni")
    workers = []

    if cfg.mode == "fanout":
        # Modalità fan-out: crea worker dedicati per ogni stanza
        for room_id in room_ids:
            room_url = f"{cfg.ws_base_url}/{room_id}"
            w = asyncio.create_task(
                fanout_worker(room_url, cfg, n_clients=min(10, clients_per_room))
            )
            workers.append(w)
            await asyncio.sleep(cfg.connect_delay * 3)

    else:
        # Modalità full o connect-only: distribuisci client nelle stanze
        for i in range(cfg.num_clients):
            room_id = room_ids[i % cfg.num_rooms]
            room_url = f"{cfg.ws_base_url}/{room_id}"
            client_id = f"load_{run_id}_{i:04d}"

            if cfg.mode == "connect-only":
                # Solo connessione, nessun invio
                async def noop_worker(url, cid):
                    async with metrics_lock:
                        metrics.connections_attempted += 1
                    try:
                        ws = await asyncio.wait_for(
                            websockets.connect(f"{url}?client_id={cid}"),
                            timeout=cfg.timeout_conn,
                        )
                        async with metrics_lock:
                            metrics.connections_ok += 1
                        await stop_event.wait()
                        await ws.close()
                    except Exception as e:
                        async with metrics_lock:
                            metrics.connections_failed += 1
                            metrics.errors.append(str(e))

                workers.append(asyncio.create_task(noop_worker(room_url, client_id)))
            else:
                workers.append(
                    asyncio.create_task(
                        client_worker(client_id, room_url, cfg, stop_event)
                    )
                )

            # Ramp-up graduale: evita spike di connessioni simultanee
            await asyncio.sleep(cfg.connect_delay)

            if (i + 1) % 10 == 0:
                async with metrics_lock:
                    ok_c = metrics.connections_ok
                    fail_c = metrics.connections_failed
                info(f"  Connessi: {ok_c}/{i+1} (falliti: {fail_c})")

    step("Fase 2 — Test in corso")
    reporter = asyncio.create_task(progress_reporter(cfg, stop_event))

    if cfg.mode != "fanout":
        await asyncio.sleep(cfg.duration_s)
        stop_event.set()

    await asyncio.gather(*workers, return_exceptions=True)
    reporter.cancel()

    metrics.end_time = time.monotonic()

    # Loss = messaggi inviati dal client ma il cui echo non è arrivato
    # (my_pending non svuotato = nessun echo ricevuto entro il grace period)
    async with metrics_lock:
        metrics.messages_lost = max(
            0, metrics.messages_sent - metrics.messages_received
        )

    step("Fase 3 — Risultati")
    print_results(cfg)


def print_results(cfg: LoadTestConfig) -> None:
    m = metrics
    elapsed = m.elapsed()

    print(
        f"""
{'='*60}
  RISULTATI TEST DI CARICO
{'='*60}

  Durata effettiva:    {elapsed:.1f}s

  CONNESSIONI
  ─────────────────────────────────────────────
  Tentate:             {m.connections_attempted}
  Riuscite:            {m.connections_ok}  ({m.success_rate():.1f}%)
  Fallite:             {m.connections_failed}
  Disconnessioni inattese: {m.disconnections_unexpected}
"""
    )

    if cfg.mode != "connect-only":
        print(
            f"""  MESSAGGI
  ─────────────────────────────────────────────
  Inviati:             {m.messages_sent}
  Ricevuti (propri):   {m.messages_received}
  Ricevuti (altri):    {m.messages_ignored}  (da altri client, ignorati correttamente)
  Persi/timeout:       {m.messages_lost}  ({m.error_rate():.2f}% loss rate)

  THROUGHPUT
  ─────────────────────────────────────────────
  Invio:               {m.throughput_sent():.1f} msg/s
  Ricezione:           {m.throughput_received():.1f} msg/s
"""
        )

    if m.rtt_samples:
        print(
            f"""  LATENZA RTT (client → backend → Redis → client)
  ─────────────────────────────────────────────
  Media:               {m.rtt_mean():.2f} ms
  p50:                 {m.rtt_p50():.2f} ms
  p95:                 {m.rtt_p95():.2f} ms
  p99:                 {m.rtt_p99():.2f} ms
  Min:                 {min(m.rtt_samples):.2f} ms
  Max:                 {max(m.rtt_samples):.2f} ms
  Campioni:            {len(m.rtt_samples)}
"""
        )

    if m.fanout_samples:
        fanout_mean = sum(m.fanout_samples) / len(m.fanout_samples)
        print(
            f"""  FAN-OUT (evento → tutti i client della stanza)
  ─────────────────────────────────────────────
  Media:               {fanout_mean:.2f} ms
  Max:                 {max(m.fanout_samples):.2f} ms
  Min:                 {min(m.fanout_samples):.2f} ms
  Campioni:            {len(m.fanout_samples)}
"""
        )

    # Analisi bottleneck
    print("  ANALISI BOTTLENECK")
    print("  ─────────────────────────────────────────────")

    if m.connections_failed > m.connections_attempted * 0.05:
        warn(f"  Alto tasso di connessioni fallite ({m.connections_failed}).")
        warn(f"  Possibile bottleneck: NGINX / Ingress controller")
        warn(f"  → Verificare: kubectl logs -n ingress-nginx <pod>")

    if m.rtt_samples and m.rtt_p95() > 200:
        warn(f"  RTT p95 elevato ({m.rtt_p95():.1f}ms).")
        warn(f"  Possibile bottleneck: Redis Pub/Sub o rete tra pod")
        warn(f"  → Verificare: latenza Redis in Grafana (game.local/grafana)")

    if m.rtt_samples and m.rtt_p99() > m.rtt_p95() * 3:
        warn(
            f"  Coda lunga RTT: p99 ({m.rtt_p99():.1f}ms) >> p95 ({m.rtt_p95():.1f}ms)."
        )
        warn(f"  Possibile bottleneck: GC pause o event loop saturo nel backend")
        warn(f"  → Verificare: CPU per istanza in Grafana")

    if m.error_rate() > 5.0:
        warn(f"  Loss rate elevato ({m.error_rate():.1f}%).")
        warn(f"  Possibile bottleneck: backend overload o buffer Redis saturo")
        warn(f"  → Verificare: ws_active_connections e redis_memory_used in Grafana")

    if m.disconnections_unexpected > 0:
        warn(f"  {m.disconnections_unexpected} disconnessioni inattese.")
        warn(f"  → Verificare: HPA (kubectl get hpa -n game) e log backend")

    if (
        (not m.rtt_samples or m.rtt_p95() < 100)
        and m.error_rate() < 1.0
        and m.connections_failed == 0
    ):
        ok("  Nessun bottleneck rilevato con questo carico")

    if m.errors:
        print(f"\n  ERRORI (primi 5)")
        print("  ─────────────────────────────────────────────")
        for e in m.errors[:5]:
            print(f"    {RED}•{NC} {e}")
        if len(m.errors) > 5:
            print(f"    ... e altri {len(m.errors)-5} errori")

    print(f"\n{'='*60}")

    # Exit code: 0 se success rate > 95% e loss < 5%
    passed = m.success_rate() >= 95.0 and m.error_rate() < 5.0
    if passed:
        print(f"  {GRN}Test superato{NC}")
    else:
        print(
            f"  {RED}Test fallito{NC} (success_rate={m.success_rate():.1f}% loss={m.error_rate():.1f}%)"
        )
    print(f"{'='*60}\n")
    sys.exit(0 if passed else 1)


def main():
    parser = argparse.ArgumentParser(description="Load test WebSocket — Task 4.1")
    parser.add_argument(
        "--url", default="ws://game.local/ws", help="URL base WebSocket"
    )
    parser.add_argument(
        "--clients", type=int, default=50, help="Numero totale di client (default: 50)"
    )
    parser.add_argument(
        "--rooms", type=int, default=5, help="Numero di stanze (default: 5)"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Durata test in secondi (default: 30)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Intervallo invio messaggi per client in s (default: 0.5)",
    )
    parser.add_argument(
        "--rampup",
        type=float,
        default=0.1,
        help="Delay tra connessioni successive (default: 0.1s)",
    )
    parser.add_argument(
        "--mode",
        default="full",
        choices=["full", "connect-only", "fanout"],
        help="Modalità: full | connect-only | fanout (default: full)",
    )
    args = parser.parse_args()

    cfg = LoadTestConfig(
        ws_base_url=args.url,
        num_clients=args.clients,
        num_rooms=args.rooms,
        duration_s=args.duration,
        send_interval=args.interval,
        connect_delay=args.rampup,
        mode=args.mode,
    )

    asyncio.run(run_load_test(cfg))


if __name__ == "__main__":
    main()
