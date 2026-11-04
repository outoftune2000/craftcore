#!/usr/bin/env python3
import asyncio
import os
import sys
import json
import time
import socket
import re
import subprocess
from installation import settings

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECURITY_DB_FILE = os.path.join(BASE_DIR, "security_db.json")
ACTIVE_PLAYERS_FILE = os.path.join(BASE_DIR, "active_players.json")
GATEKEEPER_PID_FILE = os.path.join(BASE_DIR, "gatekeeper.pid")
GATEKEEPER_LOG_FILE = os.path.join(BASE_DIR, "gatekeeper.log")
PID_FILE = os.path.join(BASE_DIR, "server.pid")
SERVER_SH = os.path.join(BASE_DIR, "server.sh")
OPS_FILE = os.path.join(BASE_DIR, "ops.json")
WHITELIST_FILE = os.path.join(BASE_DIR, "whitelist.json")

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = settings()["proxy_port"]
TARGET_HOST = "127.0.0.1"
TARGET_PORT = settings()["server_port"]

rate_limit_tracker = {}
active_sessions = {}

def daemonize():
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    
    with open(GATEKEEPER_PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    
    sys.stdout.flush()
    sys.stderr.flush()
    si = open('/dev/null', 'r')
    so = open(GATEKEEPER_LOG_FILE, 'a+')
    se = open(GATEKEEPER_LOG_FILE, 'a+')
    os.dup2(si.fileno(), sys.stdin.fileno())
    os.dup2(so.fileno(), sys.stdout.fileno())
    os.dup2(se.fileno(), sys.stderr.fileno())

def load_ops():
    if not os.path.exists(OPS_FILE):
        return []
    try:
        with open(OPS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [op.get("name", "").lower() for op in data if "name" in op]
    except Exception:
        return []

def is_minecraft_server_online():
    if not os.path.exists(PID_FILE):
        return False
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
            os.kill(pid, 0)
            return True
    except Exception:
        return False

def load_security_db():
    default_db = {
        "whitelist_approval_required": True,
        "admin_protection_enabled": True,
        "admin_session_unlocked": False,
        "auto_sleep_enabled": True,
        "auto_sleep_timeout_seconds": 300,
        "admin_passcode": "",
        "approved_users": {},
        "blocked_users": {},
        "requests_log": []
    }
    if not os.path.exists(SECURITY_DB_FILE):
        save_security_db(default_db)
        return default_db
    try:
        with open(SECURITY_DB_FILE, "r", encoding="utf-8") as f:
            db = json.load(f)
            for k, v in default_db.items():
                if k not in db:
                    db[k] = v
            return db
    except Exception:
        return default_db

def save_security_db(db):
    try:
        with open(SECURITY_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2)
    except Exception as e:
        print(f"[Gatekeeper] Error saving security DB: {e}")

def save_active_players():
    try:
        with open(ACTIVE_PLAYERS_FILE, "w", encoding="utf-8") as f:
            json.dump(active_sessions, f, indent=2)
    except Exception as e:
        print(f"[Gatekeeper] Error saving active players: {e}")

def encode_varint(val):
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val != 0:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)

def read_varint_from_buffer(buf, offset=0):
    res = 0
    shift = 0
    bytes_read = 0
    while True:
        if offset + bytes_read >= len(buf):
            return None, bytes_read
        b = buf[offset + bytes_read]
        bytes_read += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
        if shift >= 35:
            raise ValueError("VarInt too big")
    return res, bytes_read

def create_disconnect_packet(reason_text):
    reason_json = json.dumps({"text": reason_text})
    payload = encode_varint(0x00) + encode_varint(len(reason_json.encode('utf-8'))) + reason_json.encode('utf-8')
    return encode_varint(len(payload)) + payload

def create_status_response_packet(motd="§a§lCraftCore §7| §e💤 Server is Sleeping\n§b▶ Join or connect to wake up automatically!"):
    status = {
        "version": {"name": "Vanilla " + settings()["version"], "protocol": settings().get("protocol", -1)},
        "players": {"max": 20, "online": 0, "sample": []},
        "description": {"text": motd},
        "enforcesSecureChat": False
    }
    json_bytes = json.dumps(status).encode('utf-8')
    payload = encode_varint(0x00) + encode_varint(len(json_bytes)) + json_bytes
    return encode_varint(len(payload)) + payload

async def handle_sleeping_status(client_reader, client_writer):
    try:
        pkt = create_status_response_packet()
        client_writer.write(pkt)
        await client_writer.drain()
        
        # Read ping packet (0x01 + 8-byte payload)
        ping_data = await asyncio.wait_for(client_reader.read(64), timeout=2.5)
        if ping_data:
            client_writer.write(ping_data) # Send pong
            await client_writer.drain()
    except Exception:
        pass
    finally:
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass

async def pipe_streams(reader, writer, is_client_to_server=False, username=None):
    try:
        while True:
            t0 = time.time()
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
            
            # Simple latency tracking
            if username and username in active_sessions and not is_client_to_server:
                delta_ms = max(5, int((time.time() - t0) * 1000))
                # Smooth ping average
                curr_ping = active_sessions[username].get("ping_ms", 20)
                active_sessions[username]["ping_ms"] = int((curr_ping * 0.7) + (delta_ms * 0.3))
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

async def handle_client(client_reader, client_writer):
    peername = client_writer.get_extra_info('peername')
    client_ip = peername[0] if peername else "127.0.0.1"

    buffer = bytearray()
    try:
        chunk = await asyncio.wait_for(client_reader.read(4096), timeout=5.0)
        if not chunk:
            client_writer.close()
            return
        buffer.extend(chunk)
    except Exception:
        client_writer.close()
        return

    offset = 0

    if buffer.startswith(b"PROXY TCP4 "):
        try:
            end_idx = buffer.find(b"\r\n")
            if end_idx != -1:
                proxy_line = buffer[:end_idx].decode('ascii', errors='ignore')
                parts = proxy_line.split()
                if len(parts) >= 6:
                    client_ip = parts[2]
                offset = end_idx + 2
        except Exception as e:
            print(f"[Gatekeeper] PROXY header parse error: {e}")

    # Ensure full Handshake packet is in buffer
    while True:
        packet_len, len_bytes = read_varint_from_buffer(buffer, offset)
        if packet_len is not None and len(buffer) >= (offset + len_bytes + packet_len):
            break
        try:
            more = await asyncio.wait_for(client_reader.read(4096), timeout=3.0)
            if not more:
                break
            buffer.extend(more)
        except Exception:
            break

    packet_len, len_bytes = read_varint_from_buffer(buffer, offset)
    if packet_len is None:
        client_writer.close()
        return

    next_state = 1
    curr_pos = offset + len_bytes
    packet_id, id_bytes = read_varint_from_buffer(buffer, curr_pos)
    if packet_id == 0x00:
        curr_pos += id_bytes
        proto_ver, proto_bytes = read_varint_from_buffer(buffer, curr_pos)
        curr_pos += proto_bytes
        addr_len, addr_bytes = read_varint_from_buffer(buffer, curr_pos)
        curr_pos += addr_bytes + addr_len
        curr_pos += 2
        state_val, _ = read_varint_from_buffer(buffer, curr_pos)
        if state_val is not None:
            next_state = state_val

    # Status Ping (State 1) -> Forward if running, else respond sleeping MotD
    if next_state == 1:
        if is_minecraft_server_online():
            try:
                await forward_to_server(client_reader, client_writer, bytes(buffer), is_op=False, username="StatusPing", client_ip=client_ip, is_status=True)
                return
            except Exception:
                pass
        await handle_sleeping_status(client_reader, client_writer)
        return

    # Login (State 2) -> Read Login Start packet
    login_start_offset = offset + len_bytes + packet_len

    while True:
        if len(buffer) > login_start_offset:
            login_pkt_len, l_bytes = read_varint_from_buffer(buffer, login_start_offset)
            if login_pkt_len is not None and len(buffer) >= (login_start_offset + l_bytes + login_pkt_len):
                break
        try:
            more = await asyncio.wait_for(client_reader.read(4096), timeout=3.0)
            if not more:
                break
            buffer.extend(more)
        except Exception:
            break

    username = "Player"
    try:
        if len(buffer) > login_start_offset:
            login_pkt_len, l_bytes = read_varint_from_buffer(buffer, login_start_offset)
            if login_pkt_len is not None:
                p_id, p_id_bytes = read_varint_from_buffer(buffer, login_start_offset + l_bytes)
                if p_id == 0x00: # Login Start
                    u_len_pos = login_start_offset + l_bytes + p_id_bytes
                    u_len, u_bytes = read_varint_from_buffer(buffer, u_len_pos)
                    if u_len and 0 < u_len <= 32:
                        u_start = u_len_pos + u_bytes
                        raw_user = buffer[u_start:u_start + u_len].decode('utf-8', errors='ignore')
                        if raw_user:
                            username = raw_user.strip()
    except Exception as e:
        print(f"[Gatekeeper] Login Start parse error: {e}")

    if not re.fullmatch(r'[A-Za-z0-9_]{1,16}', username):
        client_writer.close()
        return

    # Anti-Enumeration Rate Limit
    now = time.time()
    user_lower = username.lower()
    timestamps = [t for t in rate_limit_tracker.get(user_lower, []) if now - t < 10.0]
    if len(timestamps) >= 4:
        msg = "§c§l[CraftCore Security]\n§fToo many connection attempts for this username. Please wait a moment."
        pkt = create_disconnect_packet(msg)
        try:
            client_writer.write(pkt)
            await client_writer.drain()
        except Exception:
            pass
        client_writer.close()
        return

    timestamps.append(now)
    rate_limit_tracker[user_lower] = timestamps

    db = load_security_db()
    ops_list = load_ops()
    is_op = user_lower in ops_list

    whitelist_required = db.get("whitelist_approval_required", True)
    admin_prot_enabled = db.get("admin_protection_enabled", True)
    admin_unlocked = db.get("admin_session_unlocked", False)

    approved_users = db.get("approved_users", {})
    blocked_users = db.get("blocked_users", {})

    is_approved = user_lower in [u.lower() for u in approved_users.keys()]
    is_blocked = user_lower in [u.lower() for u in blocked_users.keys()]

    print(f"[Gatekeeper] Player '{username}' attempting to connect (is_op={is_op}, is_approved={is_approved}, admin_unlocked={admin_unlocked})")

    # 1. Blocked User Check
    if is_blocked:
        msg = f"§c§l[CraftCore Security]\n§fYour account (§c{username}§f) is §cblocked§f by the server administrator."
        pkt = create_disconnect_packet(msg)
        try:
            client_writer.write(pkt)
            await client_writer.drain()
        except Exception:
            pass
        client_writer.close()
        return

    # 2. Admin Account Impersonation Guard
    if is_op and admin_prot_enabled:
        if not admin_unlocked:
            req_id = f"admin_{user_lower}_{int(time.time())}"
            admin_req = {
                "id": req_id,
                "username": username,
                "type": "admin_attempt",
                "timestamp": int(time.time()),
                "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "admin_locked"
            }
            requests_log = [r for r in db.get("requests_log", []) if r.get("id") != req_id]
            requests_log.insert(0, admin_req)
            db["requests_log"] = requests_log[:50]
            save_security_db(db)

            msg = (
                f"§c§l[CraftCore Admin Guard]\n\n"
                f"§eAdmin protection active for §6{username}§e.\n"
                f"§fYour login attempt has been intercepted and logged.\n\n"
                f"§aTo authenticate: Open the Dashboard at §bhttp://localhost:{settings()['gui_port']}\n"
                f"§aand click §6'Unlock Admin Session'§a, then rejoin!"
            )
            pkt = create_disconnect_packet(msg)
            try:
                client_writer.write(pkt)
                await client_writer.drain()
            except Exception:
                pass
            client_writer.close()
            return

    # 3. Whitelist Approval Check (for non-OP players)
    if whitelist_required and not is_approved and not is_op:
        req_id = f"req_{user_lower}_{int(time.time())}"
        join_req = {
            "id": req_id,
            "username": username,
            "type": "whitelist_request",
            "timestamp": int(time.time()),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending"
        }

        requests_log = [r for r in db.get("requests_log", []) if not (r.get("username", "").lower() == user_lower and r.get("status") == "pending")]
        requests_log.insert(0, join_req)
        db["requests_log"] = requests_log[:50]
        save_security_db(db)

        msg = (
            f"§e§l[CraftCore Access Gatekeeper]\n\n"
            f"§fWelcome §b{username}§f!\n"
            f"§7This server requires §6Admin Whitelist Approval§7.\n\n"
            f"§aA join request has been sent to the Admin Dashboard.\n"
            f"§ePlease ask the server owner to click §a'Approve'§e, then rejoin!"
        )
        pkt = create_disconnect_packet(msg)
        try:
            client_writer.write(pkt)
            await client_writer.drain()
        except Exception:
            pass
        client_writer.close()
        return

    # Allowed connection -> Forward to Minecraft server (or wake up on demand)
    await forward_to_server(client_reader, client_writer, bytes(buffer), is_op=is_op, username=username, client_ip=client_ip, is_status=False)

async def forward_to_server(client_reader, client_writer, initial_data, is_op=False, username=None, client_ip="127.0.0.1", is_status=False):
    # Check if Minecraft server is running; if not, trigger on-demand wake-up
    server_online = is_minecraft_server_online()
    if not server_online and not is_status:
        print(f"[Gatekeeper] ⚡ Player '{username}' joined while server is sleeping! Triggering on-demand wake-up...")
        subprocess.Popen([SERVER_SH, "start"], cwd=BASE_DIR)
        
        # Poll up to 20 seconds for server to start and accept connection
        t_start = time.time()
        connected_writer = None
        connected_reader = None
        while (time.time() - t_start) < 20.0:
            await asyncio.sleep(0.5)
            try:
                connected_reader, connected_writer = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
                break
            except Exception:
                continue

        if connected_writer:
            print(f"[Gatekeeper] [✓] Server awake! Bridging player '{username}' into Minecraft...")
            if username:
                active_sessions[username] = {
                    "username": username,
                    "connected_at": int(time.time()),
                    "ip": client_ip,
                    "ping_ms": 18
                }
                save_active_players()
            try:
                connected_writer.write(initial_data)
                await connected_writer.drain()
                await asyncio.gather(
                    pipe_streams(client_reader, connected_writer, is_client_to_server=True, username=username),
                    pipe_streams(connected_reader, client_writer, is_client_to_server=False, username=username)
                )
            except Exception:
                pass
            finally:
                if username:
                    active_sessions.pop(username, None)
                    save_active_players()
                if is_op and username:
                    db = load_security_db()
                    db["admin_session_unlocked"] = False
                    save_security_db(db)
            return
        else:
            msg = (
                "§a§l[CraftCore Standby Mode]\n\n"
                "§e💤 Server was sleeping to save resources.\n"
                "§b⚡ Server is starting up right now (~10s)!\n\n"
                "§fPlease click §a'Rejoin'§f in a few seconds."
            )
            pkt = create_disconnect_packet(msg)
            try:
                client_writer.write(pkt)
                await client_writer.drain()
            except Exception:
                pass
            finally:
                client_writer.close()
            return

    if not is_status and username:
        active_sessions[username] = {
            "username": username,
            "connected_at": int(time.time()),
            "ip": client_ip,
            "ping_ms": 18
        }
        save_active_players()

    try:
        server_reader, server_writer = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
        server_writer.write(initial_data)
        await server_writer.drain()

        await asyncio.gather(
            pipe_streams(client_reader, server_writer, is_client_to_server=True, username=username),
            pipe_streams(server_reader, client_writer, is_client_to_server=False, username=username)
        )
    except Exception as e:
        print(f"[Gatekeeper] Bridge to Minecraft server error: {e}")
        try:
            msg = (
                "§a§l[CraftCore Standby Mode]\n\n"
                "§e💤 Server is starting up.\n"
                "§fPlease wait a moment and rejoin!"
            )
            pkt = create_disconnect_packet(msg)
            client_writer.write(pkt)
            await client_writer.drain()
        except Exception:
            pass
        finally:
            client_writer.close()
    finally:
        if not is_status and username:
            active_sessions.pop(username, None)
            save_active_players()

        if is_op and username:
            db = load_security_db()
            db["admin_session_unlocked"] = False
            save_security_db(db)
            print(f"[Gatekeeper] 🔒 Admin '{username}' disconnected. Admin session automatically re-locked for protection!")

async def auto_sleep_monitor():
    idle_since = None
    while True:
        await asyncio.sleep(10.0)
        try:
            db = load_security_db()
            auto_sleep = db.get("auto_sleep_enabled", True)
            timeout_sec = db.get("auto_sleep_timeout_seconds", 300)
            
            if not auto_sleep or not is_minecraft_server_online():
                idle_since = None
                continue

            if len(active_sessions) > 0:
                idle_since = None
            else:
                now = time.time()
                if idle_since is None:
                    idle_since = now
                elif (now - idle_since) >= timeout_sec:
                    print(f"[Gatekeeper] 💤 Server has been idle for > {int(timeout_sec/60)} minutes with 0 players. Entering Sleep Mode...")
                    subprocess.run([SERVER_SH, "sleep"], cwd=BASE_DIR)
                    idle_since = None
        except Exception as e:
            print(f"[Gatekeeper] Auto-sleep monitor error: {e}")

async def main():
    save_active_players()
    asyncio.create_task(auto_sleep_monitor())
    server = await asyncio.start_server(handle_client, LISTEN_HOST, LISTEN_PORT)
    print(f"[*] CraftCore Gatekeeper Proxy listening on {LISTEN_HOST}:{LISTEN_PORT} -> {TARGET_HOST}:{TARGET_PORT}")

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    if "--daemon" in sys.argv:
        daemonize()
    else:
        with open(GATEKEEPER_PID_FILE, "w") as f:
            f.write(str(os.getpid()))

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        if os.path.exists(GATEKEEPER_PID_FILE):
            os.remove(GATEKEEPER_PID_FILE)
