#!/usr/bin/env python3
import os
import asyncio
import uuid as uuid_module
from pathlib import Path
import sys
import json
import time
import re
import io
import gzip
import zlib
import struct
import shutil
import zipfile
import tempfile
import subprocess
import psutil
import glob
import tornado.web
import tornado.websocket
import tornado.ioloop
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from installation import settings, operation_lock
from dashboard.access import (ProtectedHandler, session, same_origin, bootstrap_token,
    AuthStatusHandler, LoginHandler, LogoutHandler, AdminHandler, ActivateHandler, RegisterHandler)
from dashboard.setup_api import routes as setup_routes
from dashboard import access as account_access
from dashboard.roles import metrics_for, map_for
from terrain import surface
from map_terrain import dimension as map_dimension, mesh_tile, read_spawn

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
SERVER_SH = os.path.join(BASE_DIR, "server.sh")
PROPERTIES_FILE = os.path.join(BASE_DIR, "server.properties")
LOG_FILE = os.path.join(BASE_DIR, "server.log")
PLAYIT_LOG_FILE = os.path.join(BASE_DIR, "playit.log")
PLAYIT_PID_FILE = os.path.join(BASE_DIR, "playit.pid")
PLAYIT_TOML_FILE = os.path.join(BASE_DIR, "playit.toml")
SERVER_PID_FILE = os.path.join(BASE_DIR, "server.pid")
GATEKEEPER_PID_FILE = os.path.join(BASE_DIR, "gatekeeper.pid")
GUI_PID_FILE = os.path.join(BASE_DIR, "dashboard.pid")
GUI_LOG_FILE = os.path.join(BASE_DIR, "dashboard.log")
SECURITY_DB_FILE = os.path.join(BASE_DIR, "security_db.json")
ACTIVE_PLAYERS_FILE = os.path.join(BASE_DIR, "active_players.json")
USERCACHE_FILE = os.path.join(BASE_DIR, "usercache.json")
FIFO_FILE = os.path.join(BASE_DIR, "server.fifo")
OPS_FILE = os.path.join(BASE_DIR, "ops.json")
WHITELIST_FILE = os.path.join(BASE_DIR, "whitelist.json")
BACKUPS_DIR = os.path.join(BASE_DIR, "backups")
BACKUP_META_FILE = os.path.join(BACKUPS_DIR, "backup_meta.json")
BACKUP_INTERVAL_SECONDS = 12 * 3600  # 12 hours
BACKUP_MAX_COPIES = 3  # Up to 3 copies
WORLD_DIR = os.path.join(BASE_DIR, "world")

TERRAIN_SLOTS = asyncio.Semaphore(2)
active_ws_clients = set()
last_log_offset = 0

def daemonize():
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    
    with open(GUI_PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    
    sys.stdout.flush()
    sys.stderr.flush()
    si = open('/dev/null', 'r')
    so = open(GUI_LOG_FILE, 'a+')
    se = open(GUI_LOG_FILE, 'a+')
    os.dup2(si.fileno(), sys.stdin.fileno())
    os.dup2(so.fileno(), sys.stdout.fileno())
    os.dup2(se.fileno(), sys.stderr.fileno())

def load_ops():
    if not os.path.exists(OPS_FILE):
        return []
    try:
        with open(OPS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [op.get("name", "") for op in data if "name" in op]
    except Exception:
        return []

def load_security_db():
    if not os.path.exists(SECURITY_DB_FILE):
        default_db = {
            "whitelist_approval_required": True,
            "admin_protection_enabled": True,
            "admin_session_unlocked": True,
            "admin_passcode": "",
            "approved_users": {},
            "blocked_users": {},
            "requests_log": []
        }
        save_security_db(default_db)
        return default_db
    try:
        with open(SECURITY_DB_FILE, "r", encoding="utf-8") as f:
            db = json.load(f)
            if "whitelist_approval_required" not in db:
                db["whitelist_approval_required"] = True
            if "admin_protection_enabled" not in db:
                db["admin_protection_enabled"] = True
            if "admin_session_unlocked" not in db:
                db["admin_session_unlocked"] = True
            if "approved_users" not in db:
                db["approved_users"] = {}
            if "blocked_users" not in db:
                db["blocked_users"] = {}
            if "requests_log" not in db:
                db["requests_log"] = []
            return db
    except Exception:
        return {
            "whitelist_approval_required": True,
            "admin_protection_enabled": True,
            "admin_session_unlocked": True,
            "admin_passcode": "",
            "approved_users": {},
            "blocked_users": {},
            "requests_log": []
        }

def save_security_db(db):
    try:
        with open(SECURITY_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2)
    except Exception as e:
        print(f"[Dashboard] Error saving security DB: {e}")

_pid_cache = {
    "server": (0, None),
    "gatekeeper": (0, None),
    "tunnel": (0, None)
}

def get_server_pid():
    now = time.time()
    last_check, cached_pid = _pid_cache["server"]
    if cached_pid and (now - last_check) < 3.0:
        if psutil.pid_exists(cached_pid):
            return cached_pid
    
    pid = None
    if os.path.exists(SERVER_PID_FILE):
        try:
            with open(SERVER_PID_FILE, "r") as f:
                p = int(f.read().strip())
                if psutil.pid_exists(p):
                    pid = p
        except Exception:
            pass
    
    _pid_cache["server"] = (now, pid)
    return pid

def get_gatekeeper_pid():
    now = time.time()
    last_check, cached_pid = _pid_cache["gatekeeper"]
    if cached_pid and (now - last_check) < 3.0:
        if psutil.pid_exists(cached_pid):
            return cached_pid
            
    pid = None
    if os.path.exists(GATEKEEPER_PID_FILE):
        try:
            with open(GATEKEEPER_PID_FILE, "r") as f:
                p = int(f.read().strip())
                if psutil.pid_exists(p):
                    pid = p
        except Exception:
            pass
            
    _pid_cache["gatekeeper"] = (now, pid)
    return pid

def get_tunnel_pid():
    now = time.time()
    last_check, cached_pid = _pid_cache["tunnel"]
    if cached_pid and (now - last_check) < 3.0:
        if psutil.pid_exists(cached_pid):
            return cached_pid
            
    pid = None
    if os.path.exists(PLAYIT_PID_FILE):
        try:
            with open(PLAYIT_PID_FILE, "r") as f:
                p = int(f.read().strip())
                if psutil.pid_exists(p):
                    pid = p
        except Exception:
            pass
            
    _pid_cache["tunnel"] = (now, pid)
    return pid

def extract_tunnel_address():
    if os.path.exists(PLAYIT_LOG_FILE):
        try:
            with open(PLAYIT_LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                matches = re.findall(r'([a-zA-Z0-9\.\-]+\.(?:joinmc\.link|craft\.ply\.gg|tun\.ply\.gg|playit\.gg)(?::\d+)?)', content)
                if matches:
                    return matches[-1]
        except Exception:
            pass

    if os.path.exists(PLAYIT_TOML_FILE):
        try:
            with open(PLAYIT_TOML_FILE, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip().startswith("address"):
                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            addr = parts[1].strip().strip('"').strip("'")
                            if addr:
                                return addr
        except Exception:
            pass

    return "Not configured"

def load_backup_meta():
    if not os.path.exists(BACKUPS_DIR):
        os.makedirs(BACKUPS_DIR, exist_ok=True)
    default_meta = {
        "interval_hours": 12,
        "max_copies": 3,
        "last_backup_time": 0,
        "last_backup_file": ""
    }
    if os.path.exists(BACKUP_META_FILE):
        try:
            with open(BACKUP_META_FILE, "r", encoding="utf-8") as f:
                meta = json.load(f)
                for k, v in default_meta.items():
                    if k not in meta:
                        meta[k] = v
                return meta
        except Exception:
            pass
    return default_meta

def save_backup_meta(meta):
    try:
        if not os.path.exists(BACKUPS_DIR):
            os.makedirs(BACKUPS_DIR, exist_ok=True)
        with open(BACKUP_META_FILE, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        print(f"[Backup] Error saving backup meta: {e}")

def list_world_backups():
    if not os.path.exists(BACKUPS_DIR):
        os.makedirs(BACKUPS_DIR, exist_ok=True)
    backups = []
    for f in os.listdir(BACKUPS_DIR):
        if f.endswith(".zip") and f.startswith("world_backup_"):
            fp = os.path.join(BACKUPS_DIR, f)
            try:
                stat = os.stat(fp)
                backups.append({
                    "filename": f,
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "size_bytes": stat.st_size,
                    "created_at": int(stat.st_mtime),
                    "created_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
                })
            except Exception:
                pass
    backups.sort(key=lambda x: x["created_at"], reverse=True)
    return backups

def enforce_backup_retention():
    backups = list_world_backups()
    if len(backups) > BACKUP_MAX_COPIES:
        for b in backups[BACKUP_MAX_COPIES:]:
            fp = os.path.join(BACKUPS_DIR, b["filename"])
            try:
                os.remove(fp)
                print(f"[Backup] Purged old backup beyond 3-copy limit: {fp}")
            except Exception as e:
                print(f"[Backup] Failed to delete {fp}: {e}")

def perform_world_backup(is_manual=False):
    try:
        with operation_lock():
            return _perform_world_backup(is_manual)
    except ValueError as exc:
        return False, str(exc)

def _perform_world_backup(is_manual=False):
    if not os.path.exists(WORLD_DIR):
        return False, "World directory does not exist"

    if not os.path.exists(BACKUPS_DIR):
        os.makedirs(BACKUPS_DIR, exist_ok=True)

    is_live = bool(get_server_pid())
    if is_live:
        send_server_cmd("save-off")
        send_server_cmd("save-all flush")
        time.sleep(0.5)

    timestamp_str = time.strftime("%Y%m%d_%H%M%S")
    filename = f"world_backup_{timestamp_str}.zip"
    target_zip_path = os.path.join(BACKUPS_DIR, filename)

    try:
        with zipfile.ZipFile(target_zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for root, dirs, files in os.walk(WORLD_DIR):
                for file in files:
                    if file == "session.lock":
                        continue
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, WORLD_DIR)
                    zf.write(full_path, os.path.join("world", rel_path))

        meta = load_backup_meta()
        now_ts = int(time.time())
        meta["last_backup_time"] = now_ts
        meta["last_backup_file"] = filename
        save_backup_meta(meta)

        enforce_backup_retention()
        print(f"[Backup] Created world backup: {filename} (Retention: {len(list_world_backups())}/{BACKUP_MAX_COPIES})")
        return True, filename
    except Exception as e:
        print(f"[Backup] Error creating world backup: {e}")
        return False, str(e)
    finally:
        if is_live:
            send_server_cmd("save-on")

def restore_world_backup(filename):
    from dashboard.world_jobs import import_world
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.zip', filename): return False, 'Invalid backup filename'
    path = Path(BACKUPS_DIR) / filename
    try:
        import_world(path)
        return True, 'World restored from ' + filename
    except Exception as exc:
        return False, str(exc)

async def check_scheduled_backup():
    if not settings()["complete"]:
        return
    meta = load_backup_meta()
    last_time = meta.get("last_backup_time", 0)
    now = time.time()
    
    if last_time == 0 or (now - last_time) >= BACKUP_INTERVAL_SECONDS:
        print("[Backup] ⏰ 12-hour periodic world backup interval reached. Running backup...")
        if not getattr(check_scheduled_backup, 'busy', False):
            check_scheduled_backup.busy = True
            try:
                await asyncio.to_thread(perform_world_backup, is_manual=False)
            finally:
                check_scheduled_backup.busy = False

_world_info_cache = {"last_check": 0, "data": None}

def get_world_info():
    now = time.time()
    if _world_info_cache["data"] is not None and (now - _world_info_cache["last_check"]) < 30.0:
        return _world_info_cache["data"]

    if not os.path.exists(WORLD_DIR):
        return {
            "exists": False,
            "size_mb": 0,
            "file_count": 0,
            "last_modified": "Never",
            "backups_count": 0,
            "last_backup_str": "Never",
            "next_backup_in_seconds": 0,
            "backups": []
        }
    
    total_size = 0
    file_count = 0
    latest_mtime = 0
    for root, dirs, files in os.walk(WORLD_DIR):
        for f in files:
            fp = os.path.join(root, f)
            try:
                stat = os.stat(fp)
                total_size += stat.st_size
                file_count += 1
                if stat.st_mtime > latest_mtime:
                    latest_mtime = stat.st_mtime
            except Exception:
                pass

    mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest_mtime)) if latest_mtime else "Unknown"
    
    meta = load_backup_meta()
    backups = list_world_backups()
    last_time = meta.get("last_backup_time", 0)
    now_int = int(now)
    next_in = max(0, (last_time + BACKUP_INTERVAL_SECONDS) - now_int) if last_time else 0

    data = {
        "exists": True,
        "name": "world",
        "size_mb": round(total_size / (1024 * 1024), 2),
        "file_count": file_count,
        "last_modified": mtime_str,
        "backups_count": len(backups),
        "last_backup_time": last_time,
        "last_backup_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_time)) if last_time else "Never",
        "next_backup_in_seconds": next_in,
        "backups": backups
    }
    _world_info_cache["last_check"] = now
    _world_info_cache["data"] = data
    return data

_log_players_cache = {"last_size": 0, "online": {}}

def parse_online_players_from_log():
    if not os.path.exists(LOG_FILE):
        return {}
    
    try:
        size = os.path.getsize(LOG_FILE)
        if size == _log_players_cache["last_size"]:
            return _log_players_cache["online"]

        online = {}
        with open(LOG_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            if size > 65536:
                f.seek(size - 65536)
            for line in f:
                if 'Starting minecraft server version' in line or 'Starting Minecraft server on' in line:
                    online = {}
                    continue
                
                m_join = re.search(r'\[Server thread/INFO\]:\s+([a-zA-Z0-9_]{1,16})\[/([^\]]+)\] logged in', line)
                if m_join:
                    uname = m_join.group(1)
                    ip = m_join.group(2)
                    online[uname] = {'username': uname, 'ip': ip, 'connected_at': int(time.time()), 'ping_ms': 18}
                
                m_join2 = re.search(r'\[Server thread/INFO\]:\s+([a-zA-Z0-9_]{1,16}) joined the game', line)
                if m_join2:
                    uname = m_join2.group(1)
                    if uname not in online:
                        online[uname] = {'username': uname, 'ip': '127.0.0.1', 'connected_at': int(time.time()), 'ping_ms': 18}

                m_leave = re.search(r'\[Server thread/INFO\]:\s+([a-zA-Z0-9_]{1,16}) (left the game|lost connection)', line)
                if m_leave:
                    uname = m_leave.group(1)
                    online.pop(uname, None)
                    
        _log_players_cache["last_size"] = size
        _log_players_cache["online"] = online
        return online
    except Exception:
        return _log_players_cache.get("online", {})

def get_online_players():
    if not get_server_pid():
        return []

    active_map = {}
    # Source 1: Gatekeeper active sessions
    if os.path.exists(ACTIVE_PLAYERS_FILE):
        try:
            with open(ACTIVE_PLAYERS_FILE, "r", encoding="utf-8") as f:
                active_map = json.load(f)
        except Exception:
            pass

    # Source 2: Server log ground truth
    log_players = parse_online_players_from_log()
    for uname, info in log_players.items():
        if uname not in active_map:
            active_map[uname] = info

    ops = [u.lower() for u in load_ops()]
    usercache = {}
    if os.path.exists(USERCACHE_FILE):
        try:
            with open(USERCACHE_FILE, "r", encoding="utf-8") as f:
                for entry in json.load(f):
                    usercache[entry.get("name", "").lower()] = entry.get("uuid")
        except Exception:
            pass

    players_list = []
    for username, session in active_map.items():
        u_lower = username.lower()
        uuid_str = usercache.get(u_lower)
        
        health = 20.0
        hunger = 20
        xp_level = 0
        
        if uuid_str:
            dat_paths = [
                os.path.join(WORLD_DIR, "players", "data", f"{uuid_str}.dat"),
                os.path.join(WORLD_DIR, "playerdata", f"{uuid_str}.dat")
            ]
            for p in dat_paths:
                if os.path.exists(p):
                    try:
                        with gzip.open(p, "rb") as f:
                            data = f.read()
                            idx_h = data.find(b'Health')
                            if idx_h != -1:
                                health = round(struct.unpack('>f', data[idx_h+6:idx_h+10])[0], 1)
                            idx_f = data.find(b'foodLevel')
                            if idx_f != -1:
                                hunger = struct.unpack('>i', data[idx_f+9:idx_f+13])[0]
                            idx_xp = data.find(b'XpLevel')
                            if idx_xp != -1:
                                xp_level = struct.unpack('>i', data[idx_xp+7:idx_xp+11])[0]
                    except Exception:
                        pass
                    break

        ping_ms = session.get("ping_ms", 18)
        conn_time = session.get("connected_at", int(time.time()))
        online_sec = max(0, int(time.time() - conn_time))

        players_list.append({
            "username": username,
            "uuid": uuid_str or "",
            "ping_ms": ping_ms,
            "health": max(0.0, min(health, 20.0)),
            "max_health": 20.0,
            "hunger": max(0, min(hunger, 20)),
            "max_hunger": 20,
            "xp_level": xp_level,
            "online_seconds": online_sec,
            "is_op": u_lower in ops,
            "ip": session.get("ip", "127.0.0.1")
        })

    return players_list

_proc_cache = {}

def get_cached_server_process():
    global _proc_cache
    pid = get_server_pid()
    if not pid:
        _proc_cache.clear()
        return None, None
    
    cached_proc = _proc_cache.get("proc")
    if cached_proc and cached_proc.pid == pid:
        try:
            if cached_proc.is_running():
                return pid, cached_proc
        except Exception:
            pass
    
    try:
        proc = psutil.Process(pid)
        proc.cpu_percent(interval=None)
        _proc_cache["proc"] = proc
        return pid, proc
    except Exception:
        _proc_cache.clear()
        return pid, None

_temp_stats = {
    "cpu": {"current": None, "min": None, "max": None, "label": "CPU"},
    "gpu": {"current": None, "min": None, "max": None, "label": "GPU"}
}
_temp_cache = {"last_check": 0}

def get_hardware_temperatures():
    global _temp_stats, _temp_cache
    now = time.time()
    if (now - _temp_cache.get("last_check", 0)) < 4.5 and _temp_stats["cpu"]["current"] is not None:
        return _temp_stats
    _temp_cache["last_check"] = now
    cpu_val = None
    gpu_val = None
    cpu_label = "CPU Sensor"
    gpu_label = "GPU Sensor"

    try:
        temps = psutil.sensors_temperatures()
        if temps:
            # CPU check
            for key in ["k10temp", "coretemp", "cpu_thermal", "zenpower", "acpitz", "soc_thermal"]:
                if key in temps and temps[key]:
                    for entry in temps[key]:
                        if entry.current is not None and entry.current > 0:
                            cpu_val = entry.current
                            cpu_label = f"{key} ({entry.label or 'Tctl'})"
                            break
                    if cpu_val:
                        break
            
            if not cpu_val:
                for key, entries in temps.items():
                    if "gpu" not in key.lower() and "nvme" not in key.lower() and entries:
                        for entry in entries:
                            if entry.current is not None and entry.current > 0:
                                cpu_val = entry.current
                                cpu_label = f"{key} ({entry.label or 'temp'})"
                                break
                        if cpu_val:
                            break

            # GPU check
            for key in ["amdgpu", "nouveau", "nvidia", "i915"]:
                if key in temps and temps[key]:
                    for entry in temps[key]:
                        if entry.current is not None and entry.current > 0:
                            gpu_val = entry.current
                            gpu_label = f"{key} ({entry.label or 'edge'})"
                            break
                    if gpu_val:
                        break
    except Exception:
        pass

    # Sysfs fallback for CPU
    if cpu_val is None:
        for h in glob.glob('/sys/class/hwmon/hwmon*'):
            name = ''
            try:
                with open(os.path.join(h, 'name')) as f:
                    name = f.read().strip()
            except Exception:
                pass
            if any(k in name.lower() for k in ['k10temp', 'coretemp', 'cpu']):
                for t in glob.glob(os.path.join(h, 'temp*_input')):
                    try:
                        with open(t) as f:
                            cpu_val = float(f.read().strip()) / 1000.0
                            cpu_label = name
                            break
                    except Exception:
                        pass
                if cpu_val:
                    break

    # Sysfs fallback for GPU
    if gpu_val is None:
        for h in glob.glob('/sys/class/hwmon/hwmon*'):
            name = ''
            try:
                with open(os.path.join(h, 'name')) as f:
                    name = f.read().strip()
            except Exception:
                pass
            if any(k in name.lower() for k in ['amdgpu', 'nouveau', 'nvidia', 'gpu']):
                for t in glob.glob(os.path.join(h, 'temp*_input')):
                    try:
                        with open(t) as f:
                            gpu_val = float(f.read().strip()) / 1000.0
                            gpu_label = name
                            break
                    except Exception:
                        pass
                if gpu_val:
                    break

    if cpu_val is not None:
        cpu_val = round(cpu_val, 1)
        prev_min = _temp_stats["cpu"]["min"]
        prev_max = _temp_stats["cpu"]["max"]
        _temp_stats["cpu"]["current"] = cpu_val
        _temp_stats["cpu"]["min"] = cpu_val if prev_min is None else min(prev_min, cpu_val)
        _temp_stats["cpu"]["max"] = cpu_val if prev_max is None else max(prev_max, cpu_val)
        _temp_stats["cpu"]["label"] = cpu_label

    if gpu_val is not None:
        gpu_val = round(gpu_val, 1)
        prev_min = _temp_stats["gpu"]["min"]
        prev_max = _temp_stats["gpu"]["max"]
        _temp_stats["gpu"]["current"] = gpu_val
        _temp_stats["gpu"]["min"] = gpu_val if prev_min is None else min(prev_min, gpu_val)
        _temp_stats["gpu"]["max"] = gpu_val if prev_max is None else max(prev_max, gpu_val)
        _temp_stats["gpu"]["label"] = gpu_label

    return _temp_stats

def get_server_metrics():
    sys_cpu = psutil.cpu_percent(interval=None)
    sys_mem = psutil.virtual_memory()
    disk = psutil.disk_usage(BASE_DIR)
    
    server_pid, p = get_cached_server_process()
    tunnel_pid = get_tunnel_pid()
    gatekeeper_pid = get_gatekeeper_pid()
    
    proc_cpu = 0.0
    proc_mem_mb = 0.0
    uptime_sec = 0
    
    if p:
        try:
            proc_cpu = p.cpu_percent(interval=None)
            proc_mem_mb = p.memory_info().rss / (1024 * 1024)
            uptime_sec = max(1, int(time.time() - p.create_time()))
        except Exception:
            pass

    sec_db = load_security_db()
    pending_count = sum(1 for r in sec_db.get("requests_log", []) if r.get("status") in ["pending", "admin_locked"])
    approved_count = len(sec_db.get("approved_users", {}))
    blocked_count = len(sec_db.get("blocked_users", {}))

    tunnel_addr = extract_tunnel_address()
    online_players = get_online_players()
    hardware_temps = get_hardware_temperatures()

    server_state = "stopped"
    if server_pid:
        server_state = "running"
    elif gatekeeper_pid:
        server_state = "sleeping"
    
    return {
        "server_running": bool(server_pid),
        "server_pid": server_pid,
        "server_state": server_state,
        "gatekeeper_running": bool(gatekeeper_pid),
        "gatekeeper_pid": gatekeeper_pid,
        "auto_sleep_enabled": sec_db.get("auto_sleep_enabled", True),
        "auto_sleep_timeout_seconds": sec_db.get("auto_sleep_timeout_seconds", 300),
        "edition": "Vanilla Edition",
        "software": "Mojang Official",
        "version": settings()["version"] or "Not configured",
        "server_cpu_percent": round(proc_cpu, 1),
        "server_ram_mb": round(proc_mem_mb, 1),
        "server_uptime_seconds": uptime_sec,
        "system_cpu_percent": round(sys_cpu, 1),
        "system_ram_used_mb": round((sys_mem.total - sys_mem.available) / (1024 * 1024), 1),
        "system_ram_total_mb": round(sys_mem.total / (1024 * 1024), 1),
        "system_ram_percent": round(sys_mem.percent, 1),
        "disk_used_gb": round(disk.used / (1024**3), 2),
        "disk_total_gb": round(disk.total / (1024**3), 2),
        "disk_percent": round(disk.percent, 1),
        "temperatures": hardware_temps,
        "tunnel_running": bool(tunnel_pid),
        "tunnel_pid": tunnel_pid,
        "tunnel_address": tunnel_addr,
        "online_players_count": len(online_players),
        "online_players": online_players,
        "port": settings()["proxy_port"],
        "world": get_world_info(),
        "security": {
            "whitelist_approval_required": sec_db.get("whitelist_approval_required", True),
            "admin_protection_enabled": sec_db.get("admin_protection_enabled", True),
            "admin_session_unlocked": sec_db.get("admin_session_unlocked", False),
            "auto_sleep_enabled": sec_db.get("auto_sleep_enabled", True),
            "auto_sleep_timeout_seconds": sec_db.get("auto_sleep_timeout_seconds", 300),
            "pending_count": pending_count,
            "approved_count": approved_count,
            "blocked_count": blocked_count,
            "requests_log": sec_db.get("requests_log", []),
            "approved_users": sec_db.get("approved_users", {}),
            "blocked_users": sec_db.get("blocked_users", {}),
            "ops": load_ops()
        }
    }

def read_properties():
    props = {}
    if os.path.exists(PROPERTIES_FILE):
        with open(PROPERTIES_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    props[k.strip()] = v.strip()
    return props

def write_properties(new_props):
    lines = []
    keys_written = set()
    
    if os.path.exists(PROPERTIES_FILE):
        with open(PROPERTIES_FILE, "r", encoding="utf-8") as f:
            for line in f:
                raw_line = line.strip()
                if raw_line and not raw_line.startswith("#") and "=" in raw_line:
                    k, _ = raw_line.split("=", 1)
                    k = k.strip()
                    if k in new_props:
                        lines.append(f"{k}={new_props[k]}\n")
                        keys_written.add(k)
                    else:
                        lines.append(line)
                else:
                    lines.append(line)

    for k, v in new_props.items():
        if k not in keys_written:
            lines.append(f"{k}={v}\n")

    with open(PROPERTIES_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)

def send_server_cmd(cmd_text):
    if not cmd_text:
        return False, "Empty command"
    if os.path.exists(FIFO_FILE):
        try:
            fd = os.open(FIFO_FILE, os.O_WRONLY | os.O_NONBLOCK)
            with os.fdopen(fd, "w") as f:
                f.write(cmd_text + "\n")
            return True, f"Command sent: {cmd_text}"
        except Exception as e:
            return False, f"Failed to write to pipe: {str(e)}"
    else:
        try:
            res = subprocess.run([SERVER_SH, "cmd", cmd_text], cwd=BASE_DIR, capture_output=True, text=True, timeout=5)
            return True, res.stdout.strip()
        except Exception as e:
            return False, str(e)

# REST Handlers
class StatusHandler(ProtectedHandler):
    def get(self):
        self.set_header("Content-Type", "application/json")
        self.write(metrics_for(get_server_metrics(), session(self)))

class ActionHandler(ProtectedHandler):
    def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            action = data.get("action")
            
            if action in ["start", "stop", "sleep", "wake", "restart", "tunnel-start", "tunnel-stop", "all-start", "all-stop"]:
                subprocess.Popen([SERVER_SH, action], cwd=BASE_DIR)
                self.write({"success": True, "message": f"Action '{action}' initiated"})
            else:
                self.set_status(400)
                self.write({"success": False, "error": "Invalid action"})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class CmdHandler(ProtectedHandler):
    def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            cmd = data.get("cmd", "").strip()
            if cmd.startswith("/"):
                cmd = cmd[1:]
            success, msg = send_server_cmd(cmd)
            self.write({"success": success, "message": msg})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class ConfigHandler(ProtectedHandler):
    def get(self):
        self.set_header("Content-Type", "application/json")
        props = read_properties()
        if "players-sleeping-percentage" not in props:
            props["players-sleeping-percentage"] = "50"
        for key in ("management-server-secret", "rcon.password", "management-server-tls-keystore-password"):
            props.pop(key, None)
        self.write(json.dumps(props))

    def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            for key in ("management-server-secret", "rcon.password", "management-server-tls-keystore-password"):
                data.pop(key, None)
            for key in ('server-port', 'server-ip'):
                data.pop(key, None)
            if data.get('online-mode') == 'false' and read_properties().get('online-mode') != 'false':
                raise ValueError('Use setup to explicitly confirm offline mode')
            if any('\n' in str(v) or '\r' in str(v) for v in data.values()):
                raise ValueError('Configuration values must be single-line')
            write_properties(data)
            
            sleep_pct = data.get("players-sleeping-percentage") or data.get("players_sleeping_percentage")
            if sleep_pct is not None:
                try:
                    val = int(sleep_pct)
                    if get_server_pid():
                        send_server_cmd(f"gamerule players_sleeping_percentage {val}")
                except Exception:
                    pass
                    
            self.write({"success": True, "message": "Configuration saved successfully"})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class NBTReader:
    def __init__(self, buffer):
        self.stream = io.BytesIO(buffer)
    def read_byte(self): return struct.unpack('>b', self.stream.read(1))[0]
    def read_ubyte(self): return struct.unpack('>B', self.stream.read(1))[0]
    def read_short(self): return struct.unpack('>h', self.stream.read(2))[0]
    def read_int(self): return struct.unpack('>i', self.stream.read(4))[0]
    def read_long(self): return struct.unpack('>q', self.stream.read(8))[0]
    def read_float(self): return struct.unpack('>f', self.stream.read(4))[0]
    def read_double(self): return struct.unpack('>d', self.stream.read(8))[0]
    def read_string(self):
        length = struct.unpack('>H', self.stream.read(2))[0]
        return self.stream.read(length).decode('utf-8', errors='replace')
    def parse_tag(self, tag_type):
        if tag_type == 0: return None
        elif tag_type == 1: return self.read_byte()
        elif tag_type == 2: return self.read_short()
        elif tag_type == 3: return self.read_int()
        elif tag_type == 4: return self.read_long()
        elif tag_type == 5: return self.read_float()
        elif tag_type == 6: return self.read_double()
        elif tag_type == 7: return list(self.stream.read(self.read_int()))
        elif tag_type == 8: return self.read_string()
        elif tag_type == 9:
            t = self.read_ubyte()
            length = self.read_int()
            if length <= 0: return []
            return [self.parse_tag(t) for _ in range(length)]
        elif tag_type == 10:
            c = {}
            while True:
                t = self.read_ubyte()
                if t == 0: break
                name = self.read_string()
                c[name] = self.parse_tag(t)
            return c
        elif tag_type == 11: return [self.read_int() for _ in range(self.read_int())]
        elif tag_type == 12: return [self.read_long() for _ in range(self.read_int())]
        else: raise ValueError(f'Unknown tag type: {tag_type}')
    def parse(self):
        try:
            root_type = self.read_ubyte()
            if root_type == 10:
                self.read_string()
                return self.parse_tag(10)
        except Exception as e:
            print(f'[NBT] Parse error: {e}')
        return None

def format_item_name(item_id):
    if not item_id: return 'Empty'
    return item_id.replace('minecraft:', '').replace('_', ' ').title()

def parse_nbt_item(raw_item):
    if not raw_item: return None
    item_id = raw_item.get('id', '')
    count = raw_item.get('count', raw_item.get('Count', 1))
    slot = raw_item.get('Slot', 0)
    
    components = raw_item.get('components', raw_item.get('tag', {})) or {}
    damage = components.get('minecraft:damage', components.get('Damage', 0))
    custom_name = components.get('minecraft:custom_name', components.get('display', {}).get('Name'))
    
    ench_list = []
    enchs = components.get('minecraft:enchantments', components.get('Enchantments', {}))
    if isinstance(enchs, dict):
        levels = enchs.get('levels', enchs)
        if isinstance(levels, dict):
            for k, v in levels.items():
                ench_list.append({'name': k.replace('minecraft:', '').replace('_', ' ').title(), 'level': v})
    elif isinstance(enchs, list):
        for e in enchs:
            ench_list.append({'name': str(e.get('id', '')).replace('minecraft:', '').replace('_', ' ').title(), 'level': e.get('lvl', 1)})

    return {
        'slot': slot,
        'id': item_id,
        'name': format_item_name(item_id),
        'count': count,
        'damage': damage,
        'custom_name': custom_name,
        'enchantments': ench_list
    }

def get_player_inventory_details(uuid_or_name):
    usercache = {}
    uuid_to_name = {}
    if os.path.exists(USERCACHE_FILE):
        try:
            with open(USERCACHE_FILE, "r") as f:
                for u in json.load(f):
                    usercache[u.get("name", "").lower()] = u.get("uuid")
                    uuid_to_name[u.get("uuid")] = u.get("name")
        except Exception:
            pass

    target_uuid = None
    target_name = uuid_or_name
    
    try:
        target_uuid = str(uuid_module.UUID(uuid_or_name))
    except (ValueError, TypeError, AttributeError):
        if re.fullmatch(r'[A-Za-z0-9_]{1,16}', str(uuid_or_name)) and uuid_or_name.lower() in usercache:
            try: target_uuid = str(uuid_module.UUID(usercache[uuid_or_name.lower()]))
            except (ValueError, TypeError, AttributeError): return None
    if target_uuid: target_name = uuid_to_name.get(target_uuid, target_name)

    if not target_uuid:
        return None

    p_dir = os.path.join(WORLD_DIR, "players", "data")
    fpath = os.path.join(p_dir, f"{target_uuid}.dat")
    if not os.path.exists(fpath):
        fpath = os.path.join(WORLD_DIR, "playerdata", f"{target_uuid}.dat")
        if not os.path.exists(fpath):
            return None

    try:
        with gzip.open(fpath, "rb") as f:
            data = NBTReader(f.read()).parse()
        if not data:
            return None
        
        raw_inv = data.get('Inventory', [])
        raw_ender = data.get('EnderItems', [])
        
        hotbar = [None] * 9
        main_inv = [None] * 27
        armor = {'helmet': None, 'chestplate': None, 'leggings': None, 'boots': None}
        offhand = None
        
        for r in raw_inv:
            item = parse_nbt_item(r)
            s = item['slot']
            if 0 <= s <= 8:
                hotbar[s] = item
            elif 9 <= s <= 35:
                main_inv[s - 9] = item
            elif s == 100:
                armor['boots'] = item
            elif s == 101:
                armor['leggings'] = item
            elif s == 102:
                armor['chestplate'] = item
            elif s == 103:
                armor['helmet'] = item
            elif s in [-106, 150]:
                offhand = item
                
        ender_slots = [None] * 27
        for r in raw_ender:
            item = parse_nbt_item(r)
            s = item['slot']
            if 0 <= s < 27:
                ender_slots[s] = item
                
        pos = data.get('Pos', [0,0,0])
        return {
            'username': target_name,
            'uuid': target_uuid,
            'health': round(data.get('Health', 20.0), 1),
            'food': data.get('foodLevel', 20),
            'xp_level': data.get('XpLevel', 0),
            'xp_total': data.get('XpTotal', 0),
            'score': data.get('Score', 0),
            'dimension': data.get('Dimension', 'minecraft:overworld').replace('minecraft:', ''),
            'pos': {'x': round(pos[0], 1), 'y': round(pos[1], 1), 'z': round(pos[2], 1)},
            'hotbar': hotbar,
            'main_inventory': main_inv,
            'armor': armor,
            'offhand': offhand,
            'ender_chest': ender_slots
        }
    except Exception as e:
        print(f"[Server] Error reading player inventory {target_uuid}: {e}")
        return None

def known_dashboard_players():
    names = {}
    if os.path.exists(USERCACHE_FILE):
        try:
            for entry in json.loads(Path(USERCACHE_FILE).read_text()):
                try: names[str(uuid_module.UUID(entry['uuid']))] = entry.get('name', entry['uuid'])
                except (ValueError, KeyError, TypeError): pass
        except (OSError, ValueError): pass
    players = {}
    for folder in (Path(WORLD_DIR)/'players/data', Path(WORLD_DIR)/'playerdata'):
        if folder.exists():
            for path in folder.glob('*.dat'):
                try:
                    key = str(uuid_module.UUID(path.stem))
                    players[key] = {'uuid':key,'name':names.get(key, 'Player '+key[:8])}
                except ValueError: pass
    return sorted(players.values(),key=lambda p:p['name'].casefold())

class PlayerInventoryHandler(ProtectedHandler):
    async def get(self):
        identity = session(self)
        requested = self.get_argument('uuid', None) or self.get_argument('name', None)
        if identity['role']=='player':
            if requested:
                try: target = str(uuid_module.UUID(requested))
                except ValueError:
                    players = await asyncio.to_thread(known_dashboard_players)
                    target = next((p['uuid'] for p in players if p['name'].casefold()==requested.casefold()),None)
                if target != identity['player_uuid']: raise tornado.web.HTTPError(403, 'Only your own inventory is available')
            requested = identity['player_uuid']
        if not requested: raise tornado.web.HTTPError(400, 'Player UUID is required')
        player = await asyncio.to_thread(get_player_inventory_details, requested)
        if not player: raise tornado.web.HTTPError(404, 'Saved inventory not found')
        self.write({'success':True,'player':player})

class MeHandler(ProtectedHandler):
    async def get(self):
        identity=session(self)
        player = await asyncio.to_thread(get_player_inventory_details,identity['player_uuid']) if identity['role']=='player' else None
        self.write({'user':account_access.public_identity(identity),'player':player})

class AccountsHandler(ProtectedHandler):
    async def get(self):
        self.write({'accounts':account_access.list_player_accounts(),'registrations':account_access.list_registrations(),
                    'players':await asyncio.to_thread(known_dashboard_players)})

    async def post(self):
        try:
            data=json.loads(self.request.body)
            if not isinstance(data,dict): raise ValueError('Expected an object')
            if data.get('action','create')=='create':
                players=await asyncio.to_thread(known_dashboard_players)
                player=next((p for p in players if p['uuid']==data.get('player_uuid')),None)
                if not player: raise ValueError('Select a player who has joined and has saved player data')
                result=await asyncio.to_thread(account_access.create_player_account,data.get('username'),player['uuid'],player['name'])
            elif data.get('action')=='approve':
                players=await asyncio.to_thread(known_dashboard_players)
                player=next((p for p in players if p['uuid']==data.get('player_uuid')),None)
                if not player: raise ValueError('Select the verified saved Minecraft player before approving')
                account_id=await asyncio.to_thread(account_access.approve_registration,data.get('id'),player['uuid'],player['name'])
                result={'id':account_id,'approved':True}
            elif data.get('action')=='reject':
                await asyncio.to_thread(account_access.reject_registration,data.get('id'))
                result={'rejected':True}
            elif data.get('action')=='reset':
                result=await asyncio.to_thread(account_access.reset_player_account,data.get('id'))
            else: raise ValueError('Unknown account action')
            self.write({'success':True,**result})
        except (ValueError,TypeError) as exc:
            self.set_status(400);self.write({'error':str(exc)})

    async def delete(self):
        try:
            data=json.loads(self.request.body)
            if not isinstance(data,dict): raise ValueError('Expected an object')
            await asyncio.to_thread(account_access.delete_player_account,data.get('id'))
            self.write({'success':True})
        except (ValueError,TypeError) as exc:
            self.set_status(400);self.write({'error':str(exc)})

class PlayersHandler(ProtectedHandler):
    def get(self):
        ops = []
        whitelist = []
        if os.path.exists(OPS_FILE):
            try:
                with open(OPS_FILE, "r") as f:
                    ops = json.load(f)
            except Exception:
                pass
        if os.path.exists(WHITELIST_FILE):
            try:
                with open(WHITELIST_FILE, "r") as f:
                    whitelist = json.load(f)
            except Exception:
                pass
                
        all_players = []
        if os.path.exists(USERCACHE_FILE):
            try:
                with open(USERCACHE_FILE, "r") as f:
                    all_players = json.load(f)
            except Exception:
                pass
                
        self.write({"ops": ops, "whitelist": whitelist, "all_players": all_players})

def get_map_telemetry_data(include_tiles=True):
    players_data = []
    usercache = {}
    if os.path.exists(USERCACHE_FILE):
        try:
            with open(USERCACHE_FILE, "r") as f:
                for u in json.load(f):
                    usercache[u.get("uuid")] = u.get("name")
        except Exception:
            pass

    active_names = set()
    if os.path.exists(ACTIVE_PLAYERS_FILE):
        try:
            with open(ACTIVE_PLAYERS_FILE, "r") as f:
                active_names = set(json.load(f).keys())
        except Exception:
            pass

    p_dir = os.path.join(WORLD_DIR, "players", "data")
    if not os.path.exists(p_dir):
        p_dir = os.path.join(WORLD_DIR, "playerdata")

    if os.path.exists(p_dir):
        for f in os.listdir(p_dir):
            if f.endswith(".dat"):
                uuid = f.replace(".dat", "")
                details = get_player_inventory_details(uuid)
                if details:
                    players_data.append({
                        "name": details["username"],
                        "uuid": details["uuid"],
                        "dimension": details["dimension"],
                        "pos": details["pos"],
                        "health": details["health"],
                        "food": details["food"],
                        "xp_level": details["xp_level"],
                        "is_online": details["username"] in active_names
                    })

    maps_dir = os.path.join(WORLD_DIR, "data", "minecraft", "maps")
    tiles = []
    if include_tiles and os.path.exists(maps_dir):
        for f in os.listdir(maps_dir):
            if f.endswith(".dat") and f != "last_id.dat":
                try:
                    map_id = int(f.replace(".dat", ""))
                    with gzip.open(os.path.join(maps_dir, f), "rb") as gz:
                        data = NBTReader(gz.read()).parse()
                    d = data.get("data", {})
                    colors = d.get("colors", [])
                    if len(colors) >= 16384:
                        scale = d.get("scale", 0)
                        tiles.append({
                            "id": map_id,
                            "dimension": d.get("dimension", "minecraft:overworld").replace("minecraft:", ""),
                            "xCenter": d.get("xCenter", 0),
                            "zCenter": d.get("zCenter", 0),
                            "scale": scale,
                            "widthBlocks": 128 * (2 ** scale),
                            "colors": colors[:16384]
                        })
                except Exception:
                    pass

    spawn = read_spawn(WORLD_DIR, NBTReader)
    sec_db = load_security_db()
    waypoints = sec_db.get("waypoints", [
        {"id": "spawn", "name": "World Spawn", "dimension": "overworld", "x": 0, "z": 0, "y": 64, "color": "#6366f1", "icon": "🏠"}
    ])

    waypoints = [dict(pin, x=spawn['x'], y=spawn['y'], z=spawn['z'], dimension=spawn['dimension'], color='#ffcc00')
                 if pin.get('id') == 'spawn' else pin for pin in waypoints]
    return {
        "players": players_data,
        "tiles": tiles,
        "waypoints": waypoints,
        "spawn": spawn
    }

# ==============================================================================
# MINECRAFT WORLD REGION TILE MAP ENGINE (.MCA ANVIL RENDERER)
# ==============================================================================
REGION_CACHE_DIR = os.path.join(WORLD_DIR, ".map_cache")
MAP_MESH_CACHE_DIR = os.path.join(BASE_DIR, ".state", "map-cache")
os.makedirs(REGION_CACHE_DIR, exist_ok=True)

REGION_BLOCK_COLORS = {
    'grass_block': (92, 155, 60), 'short_grass': (92, 155, 60), 'tall_grass': (92, 155, 60), 'fern': (92, 155, 60),
    'moss_block': (85, 135, 45), 'moss_carpet': (85, 135, 45), 'water': (58, 122, 224), 'flowing_water': (58, 122, 224),
    'sand': (222, 212, 156), 'sandstone': (212, 202, 146), 'red_sand': (190, 100, 45), 'red_sandstone': (180, 90, 40),
    'dirt': (134, 96, 67), 'coarse_dirt': (120, 85, 58), 'dirt_path': (155, 125, 75), 'farmland': (110, 75, 45),
    'podzol': (100, 75, 50), 'mud': (60, 50, 45), 'stone': (125, 125, 125), 'granite': (145, 95, 80),
    'diorite': (180, 180, 180), 'andesite': (115, 115, 115), 'gravel': (130, 125, 125), 'cobblestone': (100, 100, 100),
    'deepslate': (60, 60, 68), 'cobbled_deepslate': (55, 55, 62), 'tuff': (70, 72, 65), 'bedrock': (30, 30, 35),
    'snow': (240, 245, 255), 'snow_block': (240, 245, 255), 'powder_snow': (240, 245, 255), 'ice': (150, 195, 245),
    'packed_ice': (140, 185, 240), 'blue_ice': (115, 165, 235), 'oak_leaves': (45, 115, 47), 'birch_leaves': (75, 130, 60),
    'spruce_leaves': (40, 80, 50), 'jungle_leaves': (40, 120, 40), 'acacia_leaves': (85, 120, 40), 'dark_oak_leaves': (30, 75, 30),
    'mangrove_leaves': (45, 105, 35), 'cherry_leaves': (240, 175, 200), 'oak_planks': (162, 130, 78), 'birch_planks': (192, 178, 125),
    'spruce_planks': (105, 78, 48), 'oak_log': (110, 85, 50), 'spruce_log': (58, 38, 20), 'birch_log': (215, 215, 215),
    'lava': (240, 95, 25), 'flowing_lava': (240, 95, 25), 'netherrack': (115, 25, 25), 'crimson_nylium': (140, 25, 35),
    'warped_nylium': (22, 120, 115), 'basalt': (80, 80, 85), 'blackstone': (45, 42, 48), 'soul_sand': (85, 65, 55),
    'soul_soil': (75, 55, 45), 'end_stone': (225, 230, 165), 'obsidian': (20, 15, 30)
}

def create_png_bytes(width, height, rgba_data):
    png = b'\x89PNG\r\n\x1a\n'
    ihdr_data = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    ihdr_crc = zlib.crc32(b'IHDR' + ihdr_data)
    png += struct.pack('>I', len(ihdr_data)) + b'IHDR' + ihdr_data + struct.pack('>I', ihdr_crc)
    
    raw = bytearray()
    row_bytes = width * 4
    for y in range(height):
        raw.append(0)
        raw.extend(rgba_data[y * row_bytes : (y + 1) * row_bytes])
        
    compressed = zlib.compress(bytes(raw), 6)
    idat_crc = zlib.crc32(b'IDAT' + compressed)
    png += struct.pack('>I', len(compressed)) + b'IDAT' + compressed + struct.pack('>I', idat_crc)
    
    iend_crc = zlib.crc32(b'IEND')
    png += struct.pack('>I', 0) + b'IEND' + struct.pack('>I', iend_crc)
    return png

def get_dimension_region_dir(dim):
    dim = map_dimension(dim)
    if 'nether' in dim:
        p = os.path.join(WORLD_DIR, 'dimensions', 'minecraft', 'the_nether', 'region')
        if os.path.exists(p): return p
    elif 'end' in dim:
        p = os.path.join(WORLD_DIR, 'dimensions', 'minecraft', 'the_end', 'region')
        if os.path.exists(p): return p
    else:
        p = os.path.join(WORLD_DIR, 'dimensions', 'minecraft', 'overworld', 'region')
        if os.path.exists(p): return p
        fallback = os.path.join(WORLD_DIR, 'region')
        if os.path.exists(fallback): return fallback
    return None

def get_available_regions(dim='overworld'):
    reg_dir = get_dimension_region_dir(dim)
    regions = []
    if not reg_dir or not os.path.exists(reg_dir):
        return regions
    import re
    for f in os.listdir(reg_dir):
        m = re.match(r'^r\.(-?\d+)\.(-?\d+)\.mca$', f)
        if m and os.path.getsize(os.path.join(reg_dir, f)) > 8192:
            rx, rz = int(m.group(1)), int(m.group(2))
            regions.append({
                'rx': rx,
                'rz': rz,
                'revision': str(os.stat(os.path.join(reg_dir, f)).st_mtime_ns),
                'minX': rx * 512,
                'maxX': rx * 512 + 511,
                'minZ': rz * 512,
                'maxZ': rz * 512 + 511
            })
    return regions

def render_region_tile_png(dim='overworld', rx=0, rz=0, max_y=320):
    reg_dir = get_dimension_region_dir(dim)
    if not reg_dir: return None
    reg_file = os.path.join(reg_dir, f'r.{rx}.{rz}.mca')
    if not os.path.exists(reg_file): return None
    
    mtime = os.stat(reg_file).st_mtime_ns
    cache_file = os.path.join(REGION_CACHE_DIR, f'v2_{dim}_{rx}_{rz}_{max_y}_{mtime}.png')
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'rb') as f:
                return f.read()
        except Exception:
            pass
            
    img_size = 512
    raw_pixels = bytearray(img_size * img_size * 4)
    
    # Default background
    bg_color = (15, 23, 42)
    if 'nether' in dim: bg_color = (28, 5, 5)
    elif 'end' in dim: bg_color = (11, 4, 20)
    
    for i in range(img_size * img_size):
        raw_pixels[i*4] = bg_color[0]
        raw_pixels[i*4+1] = bg_color[1]
        raw_pixels[i*4+2] = bg_color[2]
        raw_pixels[i*4+3] = 255
        
    try:
        with open(reg_file, 'rb') as f:
            header = f.read(4096)
            offsets = struct.unpack('>1024I', header)
            
            for idx, loc in enumerate(offsets):
                offset = loc >> 8
                if offset == 0: continue
                cx = idx % 32
                cz = idx // 32
                
                f.seek(offset * 4096)
                chunk_len = struct.unpack('>I', f.read(4))[0]
                if chunk_len <= 1: continue
                compression = f.read(1)[0]
                raw = f.read(chunk_len - 1)
                
                try:
                    if compression == 1: raw = gzip.decompress(raw)
                    elif compression == 2: raw = zlib.decompress(raw)
                    elif compression != 3: continue
                    chunk_nbt = NBTReader(raw).parse()
                    for (x, z), (height, block) in surface(chunk_nbt, max_y).items():
                        col = REGION_BLOCK_COLORS.get(block, (110, 110, 110))
                        shade = max(0.65, min(1.35, 0.72 + height / 160.0))
                        p_idx = ((cz * 16 + z) * img_size + cx * 16 + x) * 4
                        raw_pixels[p_idx:p_idx+4] = bytes([min(255, int(c * shade)) for c in col] + [255])
                except Exception:
                    pass
    except Exception as e:
        print(f'[RegionRenderer] Error rendering r.{rx}.{rz}.mca: {e}')
        
    png = create_png_bytes(img_size, img_size, raw_pixels)
    for previous in glob.glob(os.path.join(REGION_CACHE_DIR, f'v2_{dim}_{rx}_{rz}_{max_y}_*.png')):
        if previous != cache_file:
            try: os.unlink(previous)
            except OSError: pass
    try:
        with open(cache_file, 'wb') as f:
            f.write(png)
    except Exception:
        pass
        
    return png

class MapRegionsHandler(ProtectedHandler):
    def get(self):
        try: dim = map_dimension(self.get_argument("dim", "overworld").strip())
        except ValueError as exc: raise tornado.web.HTTPError(400, str(exc))
        regions = get_available_regions(dim)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"dimension": dim, "regions": regions}))

class MapRegionTileHandler(ProtectedHandler):
    async def get(self):
        try: dim = map_dimension(self.get_argument("dim", "overworld").strip())
        except ValueError as exc: raise tornado.web.HTTPError(400, str(exc))
        rx = int(self.get_argument("rx", "0"))
        rz = int(self.get_argument("rz", "0"))
        layer = int(self.get_argument("layer", "320"))
        
        if dim not in ('overworld', 'the_nether', 'the_end') or not -64 <= layer <= 512:
            raise tornado.web.HTTPError(400)
        async with TERRAIN_SLOTS:
            png_data = await asyncio.to_thread(render_region_tile_png, dim, rx, rz, layer)
        if not png_data:
            self.set_status(404)
            self.write("Region not found")
            return
            
        self.set_header("Content-Type", "image/png")
        self.set_header("Cache-Control", "no-cache")
        self.write(png_data)

class MapTerrainHandler(ProtectedHandler):
    def initialize(self):
        self.disconnected = False

    def on_connection_close(self):
        self.disconnected = True

    async def get(self):
        try:
            dim = map_dimension(self.get_argument('dim', 'overworld'))
            rx, rz = int(self.get_argument('rx', '0')), int(self.get_argument('rz', '0'))
            step, layer = int(self.get_argument('step', '8')), int(self.get_argument('layer', '320'))
            async with TERRAIN_SLOTS:
                if self.disconnected: return
                tile = await asyncio.to_thread(mesh_tile, get_dimension_region_dir(dim), MAP_MESH_CACHE_DIR,
                    dim, rx, rz, step, layer, NBTReader, REGION_BLOCK_COLORS)
        except ValueError as exc: raise tornado.web.HTTPError(400, str(exc))
        if self.disconnected: return
        if tile is None: raise tornado.web.HTTPError(404, 'No saved terrain in this region')
        self.set_header('Cache-Control', 'no-cache')
        self.write(tile)

class MapDataHandler(ProtectedHandler):
    async def get(self):
        self.set_header("Content-Type", "application/json")
        self.write(map_for(await asyncio.to_thread(get_map_telemetry_data, include_tiles=self.get_argument("terrain_only", "0") != "1"), session(self)))

class MapWaypointsHandler(ProtectedHandler):
    def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            sec_db = load_security_db()
            waypoints = sec_db.get("waypoints", [
                {"id": "spawn", "name": "World Spawn", "dimension": "overworld", "x": 0, "z": 0, "y": 64, "color": "#6366f1", "icon": "🏠"}
            ])
            new_wp = {
                "id": str(int(time.time() * 1000)),
                "name": data.get("name", "Waypoint"),
                "dimension": data.get("dimension", "overworld"),
                "x": float(data.get("x", 0)),
                "y": float(data.get("y", 64)),
                "z": float(data.get("z", 0)),
                "color": data.get("color", "#6366f1"),
                "icon": data.get("icon", "📍")
            }
            waypoints.append(new_wp)
            sec_db["waypoints"] = waypoints
            save_security_db(sec_db)
            self.write({"success": True, "waypoint": new_wp})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

    def delete(self):
        try:
            wp_id = self.get_argument("id", None)
            if not wp_id:
                self.set_status(400)
                self.write({"success": False, "error": "Waypoint ID required"})
                return
            sec_db = load_security_db()
            waypoints = sec_db.get("waypoints", [])
            sec_db["waypoints"] = [w for w in waypoints if w.get("id") != wp_id]
            save_security_db(sec_db)
            self.write({"success": True, "message": "Waypoint deleted"})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class WorldInfoHandler(ProtectedHandler):
    def get(self):
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(get_world_info()))

class WorldExportHandler(ProtectedHandler):
    async def get(self):
        from dashboard.world_jobs import export_world
        name = await asyncio.to_thread(export_world)
        try:
            self.set_header('Content-Type', 'application/zip')
            self.set_header('Content-Disposition', 'attachment; filename="world.zip"')
            with open(name, 'rb') as stream:
                while True:
                    chunk = await asyncio.to_thread(stream.read, 65536)
                    if not chunk: break
                    self.write(chunk)
                    await self.flush()
        finally: os.unlink(name)

class WorldImportHandler(ProtectedHandler):
    async def post(self):
        from dashboard.world_jobs import import_world
        if 'world_zip' not in self.request.files:
            raise tornado.web.HTTPError(400, 'World ZIP is required')
        try:
            await asyncio.to_thread(import_world, self.request.files['world_zip'][0]['body'])
            self.write({'success': True, 'message': 'World imported', 'world': get_world_info()})
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            raise tornado.web.HTTPError(400, str(exc))

# Security & Approvals Handlers
class SecurityStatusHandler(ProtectedHandler):
    def get(self):
        self.set_header("Content-Type", "application/json")
        db = load_security_db()
        db.pop("admin_passcode", None)
        db["ops"] = load_ops()
        self.write(json.dumps(db))

class SecurityApproveHandler(ProtectedHandler):
    def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            username = data.get("username", "").strip()
            note = data.get("note", "Approved via Dashboard")
            if not username:
                self.set_status(400)
                self.write({"success": False, "error": "Username required"})
                return

            db = load_security_db()
            u_lower = username.lower()

            # Clean from blocked users (case-insensitive)
            db["blocked_users"] = {k: v for k, v in db.get("blocked_users", {}).items() if k.lower() != u_lower}

            # Add to approved users
            db["approved_users"][username] = {
                "approved_at": int(time.time()),
                "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
                "note": note
            }

            for r in db.get("requests_log", []):
                if r.get("username", "").lower() == u_lower:
                    r["status"] = "approved"

            save_security_db(db)
            self.write({"success": True, "message": f"Player '{username}' approved successfully!"})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class SecurityDenyHandler(ProtectedHandler):
    def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            username = data.get("username", "").strip()
            reason = data.get("reason", "Blocked by Admin")
            if not username:
                self.set_status(400)
                self.write({"success": False, "error": "Username required"})
                return

            db = load_security_db()
            u_lower = username.lower()

            # Clean from approved users (case-insensitive)
            db["approved_users"] = {k: v for k, v in db.get("approved_users", {}).items() if k.lower() != u_lower}

            # Add to blocked users
            db["blocked_users"][username] = {
                "blocked_at": int(time.time()),
                "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": reason
            }

            for r in db.get("requests_log", []):
                if r.get("username", "").lower() == u_lower:
                    r["status"] = "blocked"

            save_security_db(db)
            self.write({"success": True, "message": f"Player '{username}' blocked."})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class SecurityDismissHandler(ProtectedHandler):
    def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            req_id = data.get("id", "").strip()
            username = data.get("username", "").strip()
            
            db = load_security_db()
            logs = db.get("requests_log", [])
            
            if req_id:
                db["requests_log"] = [r for r in logs if r.get("id") != req_id]
            elif username:
                u_lower = username.lower()
                db["requests_log"] = [r for r in logs if r.get("username", "").lower() != u_lower]
                
            save_security_db(db)
            self.write({"success": True, "message": "Request dismissed from log."})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class SecurityRemoveHandler(ProtectedHandler):
    def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            username = data.get("username", "").strip()
            db = load_security_db()
            u_lower = username.lower()
            
            db["approved_users"] = {k: v for k, v in db.get("approved_users", {}).items() if k.lower() != u_lower}
            db["blocked_users"] = {k: v for k, v in db.get("blocked_users", {}).items() if k.lower() != u_lower}
            
            save_security_db(db)
            self.write({"success": True, "message": f"Player '{username}' removed from list."})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class SecurityToggleAdminSessionHandler(ProtectedHandler):
    def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            db = load_security_db()
            db["admin_session_unlocked"] = bool(data.get("unlocked", True))
            save_security_db(db)
            state = "Unlocked" if db["admin_session_unlocked"] else "Locked"
            self.write({"success": True, "message": f"Admin Login Session is now {state}!", "unlocked": db["admin_session_unlocked"]})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class SecurityToggleHandler(ProtectedHandler):
    def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            db = load_security_db()
            if "whitelist_approval_required" in data:
                db["whitelist_approval_required"] = bool(data["whitelist_approval_required"])
            if "admin_protection_enabled" in data:
                db["admin_protection_enabled"] = bool(data["admin_protection_enabled"])
            if "auto_sleep_enabled" in data:
                db["auto_sleep_enabled"] = bool(data["auto_sleep_enabled"])
            if "auto_sleep_timeout_seconds" in data:
                db["auto_sleep_timeout_seconds"] = int(data["auto_sleep_timeout_seconds"])
            save_security_db(db)
            self.write({"success": True, "message": "Security rules updated successfully!"})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class LiveWebSocket(tornado.websocket.WebSocketHandler):
    def prepare(self):
        if not session(self) or not settings()['complete']:
            raise tornado.web.HTTPError(401)
        if not self.request.headers.get('Origin') or not same_origin(self, self.request.headers['Origin']):
            raise tornado.web.HTTPError(403)

    def check_origin(self, origin):
        return same_origin(self, origin)

    def open(self):
        if not session(self): self.close(4401, 'Session expired'); return
        self.expiry_timer = tornado.ioloop.PeriodicCallback(self.expire_session, 30000)
        self.expiry_timer.start()
        active_ws_clients.add(self)
        self.write_message(json.dumps({
            "type": "metrics",
            "data": metrics_for(get_server_metrics(), session(self))
        }))
        if session(self).get('role')=='admin' and os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                    recent = lines[-60:] if len(lines) > 60 else lines
                    self.write_message(json.dumps({
                        "type": "log_history",
                        "lines": recent
                    }))
            except Exception:
                pass

    def on_message(self, message):
        if not session(self):
            self.close(4401, 'Session expired'); return
        try:
            msg = json.loads(message)
            if msg.get("type") == "cmd":
                if session(self).get('role')!='admin':
                    self.write_message(json.dumps({'type':'error','message':'Administrator access required'})); return
                cmd = msg.get("cmd", "").strip()
                if cmd.startswith("/"):
                    cmd = cmd[1:]
                success, resp = send_server_cmd(cmd)
                self.write_message(json.dumps({
                    "type": "cmd_response",
                    "success": success,
                    "message": resp
                }))
        except Exception as e:
            self.write_message(json.dumps({"type": "error", "message": str(e)}))

    def expire_session(self):
        if not session(self): self.close(4401, 'Session expired')

    def on_close(self):
        if hasattr(self, 'expiry_timer'): self.expiry_timer.stop()
        active_ws_clients.discard(self)

def broadcast_metrics():
    if not active_ws_clients:
        return
    metrics = get_server_metrics()
    msg = json.dumps({"type": "metrics", "data": metrics})
    for client in list(active_ws_clients):
        try:
            if not session(client):
                client.close(4401, 'Session expired')
                active_ws_clients.discard(client)
                continue
            client.write_message(json.dumps({"type":"metrics","data":metrics_for(metrics,session(client))}))
        except Exception:
            active_ws_clients.discard(client)

def broadcast_logs():
    global last_log_offset
    if not active_ws_clients:
        return
    if not os.path.exists(LOG_FILE):
        return
    try:
        current_size = os.path.getsize(LOG_FILE)
        if current_size < last_log_offset:
            last_log_offset = 0
            
        if current_size > last_log_offset:
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(last_log_offset)
                new_content = f.read()
                last_log_offset = f.tell()
                if new_content:
                    lines = new_content.splitlines(keepends=True)
                    msg = json.dumps({"type": "log", "lines": lines})
                    for client in list(active_ws_clients):
                        try:
                            identity=session(client)
                            if not identity:
                                client.close(4401,'Session expired');continue
                            if identity['role']!='admin': continue
                            client.write_message(msg)
                        except Exception:
                            active_ws_clients.discard(client)
    except Exception:
        pass

# World Backups Handlers
class BackupsListHandler(ProtectedHandler):
    def get(self):
        meta = load_backup_meta()
        backups = list_world_backups()
        last_time = meta.get("last_backup_time", 0)
        now = int(time.time())
        next_in = max(0, (last_time + BACKUP_INTERVAL_SECONDS) - now) if last_time else 0
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({
            "interval_hours": 12,
            "max_copies": 3,
            "last_backup_time": last_time,
            "last_backup_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_time)) if last_time else "Never",
            "next_backup_in_seconds": next_in,
            "count": len(backups),
            "backups": backups
        }))

class BackupCreateHandler(ProtectedHandler):
    async def post(self):
        try:
            success, res = await asyncio.to_thread(perform_world_backup, is_manual=True)
            if success:
                self.write({"success": True, "message": f"World backup created: {res}", "filename": res})
            else:
                self.set_status(500)
                self.write({"success": False, "error": res})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class BackupRestoreHandler(ProtectedHandler):
    async def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            filename = data.get("filename", "").strip()
            if not filename:
                self.set_status(400)
                self.write({"success": False, "error": "Filename is required"})
                return

            success, msg = await asyncio.to_thread(restore_world_backup, filename)
            if success:
                self.write({"success": True, "message": msg})
            else:
                self.set_status(500)
                self.write({"success": False, "error": msg})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class BackupDeleteHandler(ProtectedHandler):
    def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            filename = data.get("filename", "").strip()
            if not filename or "/" in filename or "\\" in filename or not filename.endswith(".zip"):
                self.set_status(400)
                self.write({"success": False, "error": "Invalid filename"})
                return
            fp = os.path.join(BACKUPS_DIR, filename)
            if os.path.exists(fp):
                os.remove(fp)
                self.write({"success": True, "message": f"Deleted backup '{filename}'"})
            else:
                self.set_status(404)
                self.write({"success": False, "error": "Backup file not found"})
        except Exception as e:
            self.set_status(500)
            self.write({"success": False, "error": str(e)})

class BackupDownloadHandler(ProtectedHandler):
    def get(self):
        filename = self.get_argument("file", "").strip()
        if not filename or "/" in filename or "\\" in filename or not filename.endswith(".zip"):
            self.set_status(400)
            self.write("Invalid filename")
            return
        fp = os.path.join(BACKUPS_DIR, filename)
        if not os.path.exists(fp):
            self.set_status(404)
            self.write("File not found")
            return
        
        self.set_header("Content-Type", "application/zip")
        self.set_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.set_header("Content-Length", os.path.getsize(fp))
        
        with open(fp, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.write(chunk)
        self.finish()

class NoCacheStaticFileHandler(tornado.web.StaticFileHandler):
    def prepare(self):
        self.xsrf_token
        path = self.request.path
        if path == '/register':
            self.path_args = ['register.html']; return
        if path == '/join':
            self.path_args = ['join.html']; return
        if path in ('/login', '/setup'):
            if session(self) and session(self)['role']=='player': self.redirect('/'); return
            self.path_args = ['setup.html']
            return
        if path in ('/setup.html', '/setup.js', '/setup.css', '/session.js', '/join.html', '/join.js', '/register.html', '/register.js', '/arcade.css', '/fonts/inter.woff2', '/fonts/pixel.woff2'):
            return
        if not session(self):
            self.redirect('/login'); return
        if not settings()['complete']:
            self.redirect('/setup'); return
        if session(self)['role']=='player' and path in ('/', '/index.html'):
            self.path_args=['player.html']
        if session(self)['role']=='player' and path in ('/app.js','/accounts.js'):
            raise tornado.web.HTTPError(403)

    def set_extra_headers(self, path):
        self.set_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.set_header("Pragma", "no-cache")
        self.set_header("Expires", "0")

def make_app():
    return tornado.web.Application([
        (r"/api/auth/status", AuthStatusHandler),
        (r"/api/auth/login", LoginHandler),
        (r"/api/auth/logout", LogoutHandler),
        (r"/api/auth/activate", ActivateHandler),
        (r"/api/auth/register", RegisterHandler),
        (r"/api/me", MeHandler),
        (r"/api/accounts", AccountsHandler),
        (r"/api/setup/admin", AdminHandler),
        *setup_routes(),
        (r"/api/status", StatusHandler),
        (r"/api/action", ActionHandler),
        (r"/api/cmd", CmdHandler),
        (r"/api/config", ConfigHandler),
        (r"/api/players", PlayersHandler),
        (r"/api/players/inventory", PlayerInventoryHandler),
        (r"/api/world/info", WorldInfoHandler),
        (r"/api/world/export", WorldExportHandler),
        (r"/api/world/import", WorldImportHandler),
        (r"/api/backups", BackupsListHandler),
        (r"/api/backups/create", BackupCreateHandler),
        (r"/api/backups/restore", BackupRestoreHandler),
        (r"/api/backups/delete", BackupDeleteHandler),
        (r"/api/backups/download", BackupDownloadHandler),
        (r"/api/security/status", SecurityStatusHandler),
        (r"/api/security/approve", SecurityApproveHandler),
        (r"/api/security/deny", SecurityDenyHandler),
        (r"/api/security/dismiss", SecurityDismissHandler),
        (r"/api/security/remove", SecurityRemoveHandler),
        (r"/api/security/toggle_admin_session", SecurityToggleAdminSessionHandler),
        (r"/api/security/toggle", SecurityToggleHandler),
        (r"/api/map/data", MapDataHandler),
        (r"/api/map/terrain", MapTerrainHandler),
        (r"/api/map/regions", MapRegionsHandler),
        (r"/api/map/region_tile", MapRegionTileHandler),
        (r"/api/map/waypoints", MapWaypointsHandler),
        (r"/ws/live", LiveWebSocket),
        (r"/(.*)", NoCacheStaticFileHandler, {"path": STATIC_DIR, "default_filename": "index.html"}),
    ], xsrf_cookies=True, websocket_max_message_size=65536, compress_response=True)

def main():
    global last_log_offset
    if "--daemon" in sys.argv:
        daemonize()

    with open(GUI_PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    if os.path.exists(LOG_FILE):
        last_log_offset = os.path.getsize(LOG_FILE)
        
    port = settings()["gui_port"]
    app = make_app()
    app.listen(port, address=settings()["gui_host"], max_buffer_size=500 * 1024 * 1024)
    print(f"[*] Minecraft GUI Dashboard listening at http://127.0.0.1:{port}")
    
    if not (Path(BASE_DIR) / '.state' / 'admin.json').exists():
        print('First-run setup token: ' + bootstrap_token(), flush=True)
    # Backup compression must never hold up HTTP startup.
    tornado.ioloop.IOLoop.current().spawn_callback(check_scheduled_backup)

    tornado.ioloop.PeriodicCallback(broadcast_metrics, 1500).start()
    tornado.ioloop.PeriodicCallback(broadcast_logs, 500).start()
    tornado.ioloop.PeriodicCallback(check_scheduled_backup, 60000).start()
    
    tornado.ioloop.IOLoop.current().start()

if __name__ == "__main__":
    main()
