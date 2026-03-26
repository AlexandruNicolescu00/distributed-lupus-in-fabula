# tests/sio_client.py
# ─────────────────────────────────────────────────────────────────────────────
# Client Socket.IO helper condiviso tra test_multi_instance.py,
# test_reconnection.py, load_test.py e diag_loss.py.
# ─────────────────────────────────────────────────────────────────────────────
import asyncio
import time
import uuid
from typing import Optional

try:
    import socketio
except ImportError:
    raise ImportError("Installa le dipendenze: pip install python-socketio[asyncio-client]")


class SIOClient:
    """
    Client Socket.IO asincrono per i test.
    Connette a un server Socket.IO, raccoglie gli eventi in una coda
    e fornisce metodi helper per send/wait/drain.
    """

    def __init__(
        self,
        name:      str,
        base_url:  str,         # es. "http://game.local"
        room_id:   str,
        client_id: Optional[str] = None,
        reconnection: bool = False,   # False nei test per conteggio preciso
    ):
        self.name      = name
        self.base_url  = base_url
        self.room_id   = room_id
        self.client_id = client_id or f"test_{uuid.uuid4().hex[:8]}"
        self.instance_id: Optional[str] = None   # compilato al primo evento

        self.received: asyncio.Queue = asyncio.Queue()
        self._sio = socketio.AsyncClient(reconnection=reconnection, logger=False)
        self._connected = asyncio.Event()

        # Registra catch-all per raccogliere tutti gli eventi
        @self._sio.on("*")
        async def catch_all(event, data):
            await self.received.put({"event_type": event, **(data if isinstance(data, dict) else {"raw": data})})
            # Compila instance_id dal primo evento ricevuto
            if self.instance_id is None and "instance_id" in (data or {}):
                self.instance_id = data["instance_id"]

        @self._sio.event
        async def connect():
            self._connected.set()

    async def connect(self) -> None:
        await self._sio.connect(
            self.base_url,
            auth={"client_id": self.client_id, "room_id": self.room_id},
            transports=["websocket"],   # forza WS, evita long-polling nei test
            wait_timeout=10,
        )
        await asyncio.wait_for(self._connected.wait(), timeout=10)
        print(f"  → {self.name} connesso | client_id={self.client_id[:16]}")

    async def reconnect(self) -> None:
        """Chiude e riapre con lo stesso client_id."""
        await self.close()
        self._connected.clear()
        await asyncio.sleep(0.5)
        await self.connect()
        print(f"  → {self.name} riconnesso | client_id={self.client_id[:16]}")

    async def send(self, event: str, payload: dict = None) -> None:
        data = {"room_id": self.room_id, **(payload or {})}
        await self._sio.emit(event, data)

    async def wait_for(
        self,
        event_type: str,
        timeout:    float = 5.0,
        test_id:    Optional[str] = None,
    ) -> Optional[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                msg = await asyncio.wait_for(self.received.get(), timeout=remaining)
                if msg.get("event_type") != event_type:
                    continue
                if test_id and msg.get("payload", {}).get("test_id") != test_id \
                           and msg.get("test_id") != test_id:
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
        try:
            await self._sio.disconnect()
        except Exception:
            pass