#!/usr/bin/env python3
# tests/diag_loss.py
# Diagnostica: verifica se il loss e' reale o un artefatto del test
import asyncio, json, uuid, sys
import websockets

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "ws://game.local/ws"


async def test_single_client():
    """1 client, 10 messaggi — verifica che il backend faccia l'eco."""
    print("\n=== Test 1: singolo client, 10 messaggi ===")
    room = f"diag_{uuid.uuid4().hex[:6]}"
    url = f"{BASE_URL}/{room}?client_id=diag_single"
    ws = await websockets.connect(url)
    await asyncio.sleep(0.3)
    # svuota join events
    try:
        while True:
            await asyncio.wait_for(ws.recv(), timeout=0.2)
    except:
        pass

    sent_ids = []
    for i in range(10):
        mid = uuid.uuid4().hex[:8]
        sent_ids.append(mid)
        await ws.send(
            json.dumps(
                {
                    "event_type": "player_action",
                    "room_id": room,
                    "payload": {"msg_id": mid, "seq": i},
                }
            )
        )
        await asyncio.sleep(0.1)

    await asyncio.sleep(1.5)

    received_ids = []
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            msg = json.loads(raw)
            if msg.get("event_type") == "player_action":
                mid = msg["payload"].get("msg_id")
                received_ids.append(mid)
                print(f"  ✓ seq={msg['payload'].get('seq')} mid={mid}")
    except:
        pass

    await ws.close()
    missing = set(sent_ids) - set(received_ids)
    print(
        f"\nInviati: {len(sent_ids)}, Ricevuti: {len(received_ids)}, Persi: {len(missing)}"
    )
    if missing:
        print(f"msg_id persi: {missing}")
    return len(missing) == 0


async def test_two_clients_same_room():
    """2 client nella stessa stanza — verifica che entrambi ricevano."""
    print("\n=== Test 2: 2 client, stessa stanza ===")
    room = f"diag_{uuid.uuid4().hex[:6]}"

    ws_a = await websockets.connect(f"{BASE_URL}/{room}?client_id=diag_A")
    await asyncio.sleep(0.1)
    ws_b = await websockets.connect(f"{BASE_URL}/{room}?client_id=diag_B")
    await asyncio.sleep(0.5)
    for ws in [ws_a, ws_b]:
        try:
            while True:
                await asyncio.wait_for(ws.recv(), timeout=0.2)
        except:
            pass

    # A invia 5 messaggi
    sent = []
    for i in range(5):
        mid = uuid.uuid4().hex[:8]
        sent.append(mid)
        await ws_a.send(
            json.dumps(
                {
                    "event_type": "player_action",
                    "room_id": room,
                    "payload": {"msg_id": mid, "sender": "A", "seq": i},
                }
            )
        )
        await asyncio.sleep(0.1)

    await asyncio.sleep(1.5)

    recv_a, recv_b = [], []
    for ws, lst in [(ws_a, recv_a), (ws_b, recv_b)]:
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                msg = json.loads(raw)
                if msg.get("event_type") == "player_action":
                    lst.append(msg["payload"].get("msg_id"))
        except:
            pass

    await ws_a.close()
    await ws_b.close()

    print(f"  Client A ricevuto: {len(recv_a)}/5  ids={recv_a}")
    print(f"  Client B ricevuto: {len(recv_b)}/5  ids={recv_b}")
    # A dovrebbe ricevere tutti i suoi (broadcast locale)
    # B dovrebbe ricevere tutti (cross-instance o stesso backend)
    ok_a = len(recv_a) == 5
    ok_b = len(recv_b) == 5
    if not ok_a:
        print("  ✗ Client A non ha ricevuto tutti i propri messaggi")
    if not ok_b:
        print("  ✗ Client B ha perso messaggi")
    return ok_a and ok_b


async def test_rapid_fire():
    """1 client, 20 messaggi senza pausa — verifica comportamento sotto burst."""
    print("\n=== Test 3: burst rapido (20 msg senza sleep) ===")
    room = f"diag_{uuid.uuid4().hex[:6]}"
    ws = await websockets.connect(f"{BASE_URL}/{room}?client_id=diag_burst")
    await asyncio.sleep(0.3)
    try:
        while True:
            await asyncio.wait_for(ws.recv(), timeout=0.2)
    except:
        pass

    sent_ids = []
    for i in range(20):
        mid = uuid.uuid4().hex[:8]
        sent_ids.append(mid)
        await ws.send(
            json.dumps(
                {
                    "event_type": "player_action",
                    "room_id": room,
                    "payload": {"msg_id": mid, "seq": i},
                }
            )
        )
        # nessun sleep tra i messaggi

    await asyncio.sleep(3.0)  # lungo grace per burst

    received_ids = []
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            msg = json.loads(raw)
            if msg.get("event_type") == "player_action":
                received_ids.append(msg["payload"].get("msg_id"))
    except:
        pass

    await ws.close()
    missing = set(sent_ids) - set(received_ids)
    print(
        f"Inviati: {len(sent_ids)}, Ricevuti: {len(received_ids)}, Persi: {len(missing)}"
    )
    if missing:
        print(f"Persi: {missing}")
    return len(missing) == 0


async def main():
    r1 = await test_single_client()
    r2 = await test_two_clients_same_room()
    r3 = await test_rapid_fire()

    print("\n=== DIAGNOSI ===")
    if r1 and r2 and r3:
        print("Backend OK — il loss nel load test e' un artefatto del test stesso")
        print("→ Il problema e' probabilmente nel recv_task che viene cancellato")
        print("  prima che tutti gli echo arrivino. Aumentare il grace period.")
    elif not r1:
        print("PROBLEMA BACKEND: il singolo client non riceve i propri echo")
        print("→ Verificare main.py: broadcast_to_room include il mittente?")
    elif not r2:
        print("PROBLEMA CROSS-CLIENT: client B non riceve echo di client A")
        print("→ Verificare Redis Pub/Sub e deduplicazione")
    elif not r3:
        print("PROBLEMA BURST: messaggi persi sotto carico rapido")
        print("→ Possibile bottleneck: event loop o buffer Redis")


asyncio.run(main())
