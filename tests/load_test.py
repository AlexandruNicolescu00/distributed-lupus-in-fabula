#!/usr/bin/env python3
# tests/load_test.py

import argparse
import asyncio
import sys
import time
import uuid
from dataclasses import dataclass, field

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


@dataclass
class Metrics:
    connections_attempted:    int = 0
    connections_ok:           int = 0
    connections_failed:       int = 0
    disconnections_unexpected:int = 0
    messages_sent:            int = 0
    messages_received:        int = 0
    rtt_samples: list = field(default_factory=list)
    errors:      list = field(default_factory=list)
    start_time:  float = 0.0
    end_time:    float = 0.0

    def elapsed(self):
        return (self.end_time or time.monotonic()) - self.start_time

    def success_rate(self):
        return (
            (self.connections_ok / self.connections_attempted * 100)
            if self.connections_attempted > 0
            else 0
        )

    def rtt_p95(self):
        if not self.rtt_samples:
            return 0
        s = sorted(self.rtt_samples)
        return s[int(0.95 * len(s))]


metrics      = Metrics()
metrics_lock = asyncio.Lock()


async def client_worker(client_id: str, room_id: str, cfg: LoadTestConfig, stop_event: asyncio.Event):
    client     = SIOClient(
        name=client_id,
        base_url=cfg.sio_url,
        room_id=room_id,
        client_id=client_id,
        reconnection=False,
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
            async with metrics_lock:
                metrics.messages_received += 1
                metrics.rtt_samples.append(
                    (time.monotonic() - my_pending.pop(msg_id)) * 1000
                )

    try:
        async with metrics_lock:
            metrics.connections_attempted += 1

        await client.connect()

        async with metrics_lock:
            metrics.connections_ok += 1

        while not stop_event.is_set():
            msg_id = uuid.uuid4().hex
            my_pending[msg_id] = time.monotonic()
            await client.send(
                "client_message",
                {"event_type": "player_action", "payload": {"msg_id": msg_id, "text": "ping"}},
            )
            async with metrics_lock:
                metrics.messages_sent += 1
            await asyncio.sleep(cfg.send_interval)

    except Exception as e:
        async with metrics_lock:
            metrics.connections_failed += 1
            metrics.errors.append(
                f"Conn Error ({client_id[-7:]}): {type(e).__name__}: {e}"
            )
    finally:
        await client.close()


async def progress_reporter(cfg: LoadTestConfig, stop_event: asyncio.Event):
    start = time.monotonic()
    while not stop_event.is_set():
        await asyncio.sleep(2)
        async with metrics_lock:
            print(
                f"  [{time.monotonic()-start:4.0f}s / {cfg.duration_s}s] "
                f"Conn: {metrics.connections_ok} | Sent: {metrics.messages_sent} | Fail: {metrics.connections_failed}"
            )


async def run_load_test(cfg: LoadTestConfig):
    print(
        f"{'='*60}\n  SIO Load Test: {cfg.num_clients} clients | {cfg.num_rooms} rooms\n{'='*60}"
    )

    metrics.start_time = time.monotonic()
    stop_event         = asyncio.Event()
    room_ids           = [f"room_{i}" for i in range(cfg.num_rooms)]

    print(f"\n{CYN}▸ Fase 1 — Avvio Client{NC}")
    reporter = asyncio.create_task(progress_reporter(cfg, stop_event))

    workers = []
    for i in range(cfg.num_clients):
        workers.append(
            asyncio.create_task(
                client_worker(
                    f"load_{i:03d}", room_ids[i % cfg.num_rooms], cfg, stop_event
                )
            )
        )
        await asyncio.sleep(cfg.connect_delay)

    await asyncio.sleep(cfg.duration_s)

    print(f"\n{CYN}▸ Fase 2 — Shutdown...{NC}")
    stop_event.set()
    await asyncio.gather(*workers, return_exceptions=True)
    reporter.cancel()
    metrics.end_time = time.monotonic()

    print_results()


def print_results():
    m = metrics
    print(f"\n{'='*60}\n RISULTATI\n{'='*60}")
    print(f" Tentate:    {m.connections_attempted}")
    print(f" Riuscite:   {m.connections_ok} ({m.success_rate():.1f}%)")
    print(f" Fallite:    {m.connections_failed}")
    if m.rtt_samples:
        print(f" RTT p95:    {m.rtt_p95():.2f} ms")
    if m.errors:
        print(f" Ultimo errore: {m.errors[-1]}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Base URL Socket.IO (es: http://game.local)",
    )
    parser.add_argument("--clients",  type=int,   default=50)
    parser.add_argument("--rooms",    type=int,   default=5)
    parser.add_argument("--duration", type=float, default=20.0)
    args = parser.parse_args()

    config = LoadTestConfig(
        sio_url=args.url,
        num_clients=args.clients,
        num_rooms=args.rooms,
        duration_s=args.duration,
        send_interval=0.5,
        connect_delay=0.05,
        mode="full",
    )

    try:
        asyncio.run(run_load_test(config))
    except KeyboardInterrupt:
        pass
