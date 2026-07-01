#!/usr/bin/env python3
# tests/load_test.py
# python tests/load_test.py --url http://game.local --clients 500 --procs 4
#
# Generatore di carico Socket.IO MULTI-PROCESSO.
# Un singolo event loop asyncio (un solo processo) satura ben prima del cluster:
# gestire centinaia di WebSocket concorrenti + i loro ping su un unico loop fa
# accodare gli handshake dietro il GIL finché scadono in timeout (falsi
# "Connection error"). Qui i client vengono spartiti su più processi, ciascuno
# col proprio event loop, così il carico è realmente simultaneo e il collo di
# bottiglia torna a essere l'infrastruttura sotto test, non lo script.

import argparse
import asyncio
import multiprocessing as mp
import os
import queue as queue_mod
import sys
import time
import uuid
from dataclasses import dataclass

from sio_client import SIOClient

# Colorazione output
GRN, RED, YLW, BLU, CYN, NC = (
    "\033[0;32m",
    "\033[0;31m",
    "\033[1;33m",
    "\033[0;34m",
    "\033[0;36m",
    "\033[0m",
)

# Chiavi dei contatori condivisi tra i processi (aggiornati live per il progress).
_SHARED_KEYS = ("conn_attempted", "conn_ok", "conn_failed", "msgs_sent", "msgs_recv")


@dataclass
class LoadTestConfig:
    sio_url:       str
    num_clients:   int
    num_rooms:     int
    duration_s:    float
    send_interval: float
    connect_delay: float
    mode:          str
    timeout_conn:  float = 10.0


class ProcMetrics:
    """Metriche locali a UN processo/event loop.

    Niente lock: in un singolo event loop le coroutine cedono il controllo solo
    sugli await, quindi gli incrementi semplici sono atomici tra loro.
    """

    def __init__(self):
        self.conn_attempted = 0
        self.conn_ok        = 0
        self.conn_failed    = 0
        self.msgs_sent      = 0
        self.msgs_recv      = 0
        self.rtt_samples: list = []
        self.errors:      list = []


async def client_worker(
    client_id: str,
    room_id: str,
    cfg: LoadTestConfig,
    stop_event: asyncio.Event,
    m: ProcMetrics,
):
    client = SIOClient(
        name=client_id,
        base_url=cfg.sio_url,
        room_id=room_id,
        client_id=client_id,
        reconnection=False,
        conn_timeout=cfg.timeout_conn,
    )
    my_pending: dict[str, float] = {}

    # Intercetta le risposte player_action per misurare RTT
    @client._sio.on("player_action")
    async def on_action(data):
        if isinstance(data, str):
            import json
            data = json.loads(data)
        msg_id = data.get("payload", {}).get("msg_id")
        if msg_id in my_pending:
            m.msgs_recv += 1
            m.rtt_samples.append((time.monotonic() - my_pending.pop(msg_id)) * 1000)

    try:
        m.conn_attempted += 1
        await client.connect()
        m.conn_ok += 1

        while not stop_event.is_set():
            msg_id = uuid.uuid4().hex
            my_pending[msg_id] = time.monotonic()
            # Emette direttamente l'evento "player_action": il backend (catch_all
            # → _broadcast_passthrough) lo ri-emette con lo stesso nome, così
            # l'handler on_action riceve l'eco e misura l'RTT. msg_id va al primo
            # livello del payload perché finisce nell'envelope WSMessage.payload.
            await client.send("player_action", {"msg_id": msg_id, "text": "ping"})
            m.msgs_sent += 1
            await asyncio.sleep(cfg.send_interval)

    except Exception as e:
        m.conn_failed += 1
        # Tiene solo gli ultimi errori per non gonfiare la coda inter-processo.
        m.errors.append(f"Conn Error ({client_id[-8:]}): {type(e).__name__}: {e}")
        if len(m.errors) > 5:
            m.errors = m.errors[-5:]
    finally:
        await client.close()


def _flush(m: ProcMetrics, shared: dict, last: dict):
    """Riversa i delta dei contatori locali nei Value condivisi (progress live)."""
    for key in _SHARED_KEYS:
        val = getattr(m, key)
        delta = val - last[key]
        if delta:
            with shared[key].get_lock():
                shared[key].value += delta
            last[key] = val


async def _flush_loop(m: ProcMetrics, shared: dict, stop: asyncio.Event):
    last = {k: 0 for k in _SHARED_KEYS}
    try:
        while not stop.is_set():
            await asyncio.sleep(0.5)
            _flush(m, shared, last)
    finally:
        _flush(m, shared, last)


async def run_proc_async(client_indices: list[int], cfg: LoadTestConfig, shared: dict) -> dict:
    m          = ProcMetrics()
    stop_event = asyncio.Event()
    room_ids   = [f"room_{i}" for i in range(cfg.num_rooms)]

    flusher = asyncio.create_task(_flush_loop(m, shared, stop_event))

    workers = []
    for idx in client_indices:
        workers.append(
            asyncio.create_task(
                client_worker(
                    f"load_{idx:04d}", room_ids[idx % cfg.num_rooms], cfg, stop_event, m
                )
            )
        )
        await asyncio.sleep(cfg.connect_delay)

    await asyncio.sleep(cfg.duration_s)

    stop_event.set()
    await asyncio.gather(*workers, return_exceptions=True)
    flusher.cancel()
    try:
        await flusher
    except asyncio.CancelledError:
        pass

    return {
        "conn_attempted": m.conn_attempted,
        "conn_ok":        m.conn_ok,
        "conn_failed":    m.conn_failed,
        "msgs_sent":      m.msgs_sent,
        "msgs_recv":      m.msgs_recv,
        "rtt_samples":    m.rtt_samples,
        "errors":         m.errors,
    }


def proc_entry(client_indices: list[int], cfg: LoadTestConfig, shared: dict, result_q: mp.Queue):
    """Entrypoint del processo figlio (deve stare a livello modulo per lo spawn)."""
    try:
        result = asyncio.run(run_proc_async(client_indices, cfg, shared))
    except Exception as e:  # non far morire il parent in attesa del risultato
        result = {
            "conn_attempted": 0, "conn_ok": 0, "conn_failed": len(client_indices),
            "msgs_sent": 0, "msgs_recv": 0, "rtt_samples": [],
            "errors": [f"Proc fatal: {type(e).__name__}: {e}"],
        }
    result_q.put(result)


def _split(num_clients: int, procs: int) -> list[list[int]]:
    """Spartisce gli indici client 0..N-1 in `procs` blocchi quasi uguali."""
    chunks = [[] for _ in range(procs)]
    for i in range(num_clients):
        chunks[i % procs].append(i)
    return [c for c in chunks if c]


def rtt_p95(samples: list) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    return s[int(0.95 * len(s))]


def print_results(agg: dict):
    attempted = agg["conn_attempted"]
    ok        = agg["conn_ok"]
    rate      = (ok / attempted * 100) if attempted else 0.0
    print(f"\n{'='*60}\n RISULTATI\n{'='*60}")
    print(f" Processi:   {agg['procs']}")
    print(f" Tentate:    {attempted}")
    print(f" Riuscite:   {ok} ({rate:.1f}%)")
    print(f" Fallite:    {agg['conn_failed']}")
    print(f" Msg inviati/ricevuti: {agg['msgs_sent']} / {agg['msgs_recv']}")
    if agg["rtt_samples"]:
        print(f" RTT p95:    {rtt_p95(agg['rtt_samples']):.2f} ms")
    if agg["errors"]:
        print(f" Ultimo errore: {agg['errors'][-1]}")
    print(f"{'='*60}\n")


def run_load_test(cfg: LoadTestConfig, procs: int):
    chunks = _split(cfg.num_clients, procs)
    procs  = len(chunks)

    per_room = -(-cfg.num_clients // cfg.num_rooms)  # ceil
    print(f"{'='*60}")
    print(f"  SIO Load Test: {cfg.num_clients} client | {cfg.num_rooms} room "
          f"(~{per_room}/room) | {procs} processi")
    print(f"  (~{cfg.num_clients // procs} client/processo, ramp ~"
          f"{(cfg.num_clients // procs) * cfg.connect_delay:.0f}s)")
    if per_room > 20:
        print(f"  {YLW}ATTENZIONE: ~{per_room} giocatori/room è irrealistico per una "
              f"partita di Lupus.\n  Il join broadcasta l'intera lista alla room "
              f"(fan-out O(n²)) e ingolfa\n  l'accettazione dei connect. Usa --room-size "
              f"per distribuire su più room.{NC}")
    print(f"{'='*60}")

    shared   = {k: mp.Value("i", 0) for k in _SHARED_KEYS}
    result_q: mp.Queue = mp.Queue()

    processes = [
        mp.Process(target=proc_entry, args=(chunk, cfg, shared, result_q))
        for chunk in chunks
    ]

    print(f"\n{CYN}▸ Fase 1 — Avvio client su {procs} processi{NC}")
    start = time.monotonic()
    for p in processes:
        p.start()

    # Raccoglie i risultati man mano che i processi finiscono (drenare la coda
    # PRIMA dei join evita il deadlock del feeder thread con payload grandi),
    # stampando intanto il progresso aggregato dai contatori condivisi.
    results: list[dict] = []
    while len(results) < len(processes):
        try:
            results.append(result_q.get(timeout=2))
        except queue_mod.Empty:
            pass
        print(
            f"  [{time.monotonic()-start:4.0f}s] "
            f"Conn: {shared['conn_ok'].value} | "
            f"Sent: {shared['msgs_sent'].value} | "
            f"Recv: {shared['msgs_recv'].value} | "
            f"Fail: {shared['conn_failed'].value}"
        )

    print(f"\n{CYN}▸ Fase 2 — Shutdown...{NC}")
    for p in processes:
        p.join()

    agg = {
        "procs": procs,
        "conn_attempted": sum(r["conn_attempted"] for r in results),
        "conn_ok":        sum(r["conn_ok"] for r in results),
        "conn_failed":    sum(r["conn_failed"] for r in results),
        "msgs_sent":      sum(r["msgs_sent"] for r in results),
        "msgs_recv":      sum(r["msgs_recv"] for r in results),
        "rtt_samples":    [x for r in results for x in r["rtt_samples"]],
        "errors":         [e for r in results for e in r["errors"]],
    }
    print_results(agg)


if __name__ == "__main__":
    mp.freeze_support()  # necessario per lo spawn su Windows

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Base URL Socket.IO (es: http://game.local)",
    )
    parser.add_argument("--clients",  type=int,   default=50)
    parser.add_argument(
        "--rooms", type=int, default=0,
        help="Numero di room (0 = auto da --room-size). Pochi client in molte "
             "room simula tanti giochi concorrenti, il workload distribuito reale.",
    )
    parser.add_argument(
        "--room-size", type=int, default=8,
        help="Giocatori per room quando --rooms=0 (default 8, partita realistica). "
             "Valori alti gonfiano il fan-out del join e falsano il test.",
    )
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument(
        "--procs", type=int, default=0,
        help="Numero di processi worker (0 = auto: ~125 client/processo, max n. CPU)",
    )
    parser.add_argument("--connect-delay", type=float, default=0.05,
                        help="Pausa tra un connect e l'altro DENTRO ogni processo")
    parser.add_argument("--send-interval", type=float, default=0.5)
    parser.add_argument("--connect-timeout", type=float, default=10.0,
                        help="Timeout handshake per connessione. Sotto burst il "
                             "server accetta ma risponde tardi: alza a 30 per non "
                             "contare come falliti gli handshake solo lenti.")
    args = parser.parse_args()

    # Auto: un processo ogni ~125 client, limitato dal numero di CPU disponibili.
    if args.procs > 0:
        procs = args.procs
    else:
        procs = max(1, min(os.cpu_count() or 4, -(-args.clients // 125)))
    procs = min(procs, args.clients)

    # Room: esplicite con --rooms, altrimenti derivate da --room-size così ogni
    # room ha un numero realistico di giocatori (niente fan-out O(n²) sul join).
    if args.rooms > 0:
        num_rooms = args.rooms
    else:
        num_rooms = max(1, -(-args.clients // args.room_size))

    config = LoadTestConfig(
        sio_url=args.url,
        num_clients=args.clients,
        num_rooms=num_rooms,
        duration_s=args.duration,
        send_interval=args.send_interval,
        connect_delay=args.connect_delay,
        mode="full",
        timeout_conn=args.connect_timeout,
    )

    try:
        run_load_test(config, procs)
    except KeyboardInterrupt:
        pass
