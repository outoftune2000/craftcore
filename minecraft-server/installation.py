"""Private installation state and verified Vanilla downloads (no shell execution)."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import urllib.parse
import zipfile

BASE = Path(__file__).resolve().parent
STATE = BASE / '.state'
MANIFEST = 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json'
LOCK = threading.RLock()
DEFAULTS = dict(version='', java='java', memory_mb=4096, proxy_port=25565,
                server_port=25566, gui_port=3000, gui_host='127.0.0.1', complete=False)

def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {} if default is None else default

def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def settings():
    return DEFAULTS | read_json(STATE / 'installation.json')

def save_settings(data):
    write_json(STATE / 'installation.json', data)

def properties():
    path = BASE / 'server.properties'
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if '=' in line and not line.startswith('#')) if path.exists() else {}

def update_properties(changes):
    path = BASE / 'server.properties'
    if not path.exists():
        shutil.copyfile(BASE / 'server.properties.example', path)
    lines = path.read_text().splitlines()
    existing = set()
    for i, line in enumerate(lines):
        if '=' in line and not line.startswith('#'):
            key = line.split('=', 1)[0]
            if key in changes:
                lines[i] = key + '=' + str(changes[key]); existing.add(key)
    lines += [key + '=' + str(value) for key, value in changes.items() if key not in existing]
    path.write_text('\n'.join(lines) + '\n'); path.chmod(0o600)

def installed_version():
    jar = BASE / 'server.jar'
    if not jar.exists(): return None
    with zipfile.ZipFile(jar) as archive:
        return json.loads(archive.read('version.json'))

def running():
    # Validate the executable and working directory, not merely a reused PID.
    import psutil
    try:
        pid = int((BASE / 'server.pid').read_text())
        proc = psutil.Process(pid)
        return proc.is_running() and Path(proc.cwd()).resolve() == BASE and '-jar' in proc.cmdline()
    except (OSError, ValueError, psutil.Error):
        return False

def process_pid(kind):
    import psutil
    filename = {'server': 'server.pid', 'gui': 'dashboard.pid', 'gatekeeper': 'gatekeeper.pid', 'playit': 'playit.pid'}[kind]
    marker = {'server': '-jar', 'gui': 'dashboard/server.py', 'gatekeeper': 'gatekeeper.py', 'playit': 'playitd'}[kind]
    try:
        pid = int((BASE / filename).read_text())
        proc = psutil.Process(pid)
        if proc.is_running() and Path(proc.cwd()).resolve() == BASE and any(marker in arg for arg in proc.cmdline()): return pid
    except (OSError, ValueError, psutil.Error): pass
    return None

@contextlib.contextmanager
def operation_lock():
    """Cross-process lock shared by installs, starts, restores and backups."""
    STATE.mkdir(exist_ok=True, mode=0o700)
    with open(STATE / 'operation.lock', 'a+b') as stream:
        import fcntl
        try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('Another server operation is in progress')
        try: yield
        finally: fcntl.flock(stream, fcntl.LOCK_UN)

def java_version(executable):
    if not isinstance(executable, str) or not executable or executable.startswith('-'):
        raise ValueError('Choose a Java executable')
    try:
        result = subprocess.run([executable, '-version'], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Java not found. Install the required OpenJDK version and enter its executable path.') from exc
    match = re.search(r'version "(\d+)(?:\.(\d+))?', result.stdout + result.stderr)
    if result.returncode or not match: raise ValueError('Could not determine Java version')
    return int(match[2] if match[1] == '1' else match[1])

def check_java(executable, required):
    actual = java_version(executable)
    if actual < required:
        raise ValueError(f'Java {required}+ is required; selected Java is {actual}. Install OpenJDK {required} and choose its executable.')
    return actual

def fetch_json(url):
    validate_url(url)
    with urllib.request.urlopen(url, timeout=30) as response:
        validate_url(response.url)
        return json.load(response)

def validate_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in {
        'piston-meta.mojang.com', 'launchermeta.mojang.com',
        'piston-data.mojang.com', 'launcher.mojang.com'}:
        raise ValueError('Only official Mojang HTTPS downloads are allowed')

def catalog(refresh=False):
    with LOCK:
        path = STATE / 'catalog.json'; cached = read_json(path)
        if cached and not refresh and cached.get('retry_after', 0) > time.time():
            return cached | {'stale': True}
        if cached and not refresh and time.time() - cached['fetched_at'] < 3600:
            return cached | {'stale': False}
        try:
            manifest = fetch_json(MANIFEST)
            value = {'fetched_at': time.time(), 'latest': manifest['latest'], 'versions': manifest['versions']}
            write_json(path, value)
            return value | {'stale': False}
        except Exception:
            if cached:
                cached['retry_after'] = time.time() + 60
                write_json(path, cached)
                return cached | {'stale': True, 'warning': 'Catalog refresh failed; showing cached versions.'}
            raise ValueError('Cannot reach Mojang. Retry when online, or import the installed server.')

def metadata(version):
    item = next((v for v in catalog()['versions'] if v['id'] == version), None)
    if not item: raise ValueError('Unknown Minecraft version')
    path = STATE / 'metadata' / (hashlib.sha256(version.encode()).hexdigest() + '.json')
    value = read_json(path)
    if not value:
        value = fetch_json(item['url']); write_json(path, value)
    if 'server' not in value.get('downloads', {}):
        raise ValueError('This version has no official server download')
    return value

def available_versions(snapshots=False, refresh=False, offset=0):
    """Stream-check metadata; only publish server-capable entries. Cache each result."""
    from concurrent.futures import ThreadPoolExecutor
    data = catalog(refresh)
    candidates = [v for v in data['versions'] if v['type'] == 'release' or (snapshots and v['type'] == 'snapshot')]
    if offset < 0: raise ValueError('Invalid catalog offset')
    page = candidates[offset:offset + 40]
    def supported(v):
        try:
            meta = metadata(v['id'])
            return v | {'java_major': meta.get('javaVersion', {}).get('majorVersion', 8)}
        except ValueError: return None
    with ThreadPoolExecutor(max_workers=12) as pool:
        versions = [v for v in pool.map(supported, page) if v]
    return {k: data[k] for k in ('fetched_at', 'stale', 'latest')} | {
        'versions': versions, 'next_offset': offset + 40 if offset + 40 < len(candidates) else None}

def validate_options(data):
    result = {}
    result['memory_mb'] = int(data.get('memory_mb', 4096))
    if not 512 <= result['memory_mb'] <= 262144: raise ValueError('RAM must be 512–262144 MB')
    result['java'] = str(data.get('java', 'java'))
    for key, default in [('proxy_port', 25565), ('server_port', 25566)]:
        result[key] = int(data.get(key, default))
        if not 1024 <= result[key] <= 65535: raise ValueError('Ports must be 1024–65535')
    if result['proxy_port'] == result['server_port']: raise ValueError('Server and proxy ports must differ')
    if data.get('gamemode', 'survival') not in ['survival', 'creative', 'adventure', 'spectator']: raise ValueError('Invalid game mode')
    if data.get('difficulty', 'easy') not in ['peaceful', 'easy', 'normal', 'hard']: raise ValueError('Invalid difficulty')
    name = str(data.get('name', 'Minecraft Server'))
    if not 1 <= len(name) <= 100 or any(c in name for c in '\r\n\\'): raise ValueError('Invalid server name')
    count = int(data.get('max_players', 20))
    if not 1 <= count <= 1000: raise ValueError('Player limit must be 1–1000')
    if data.get('online_mode', True) is not True and data.get('offline_confirm') is not True:
        raise ValueError('Explicitly confirm the risks of offline mode')
    return result

def backup_world():
    world = BASE / 'world'
    if not world.exists(): return
    folder = BASE / 'backups'; folder.mkdir(exist_ok=True)
    target = folder / ('before-version-change-' + str(time.time_ns()) + '.zip')
    tmp = target.with_suffix('.part')
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in world.rglob('*'):
                if path.is_file() and path.name != 'session.lock': archive.write(path, path.relative_to(BASE))
        with zipfile.ZipFile(tmp) as archive:
            if archive.testzip(): raise ValueError('World backup verification failed')
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)

def import_existing():
    with operation_lock():
        if settings()['complete']: raise ValueError('Setup is already complete')
        version = installed_version()
        if not version: raise ValueError('No installed server.jar found')
        props = properties(); state = settings()
        backup = STATE / ('import-backup-' + str(time.time_ns())); backup.mkdir()
        for name in ('server.properties', 'eula.txt', '.env'):
            if (BASE / name).exists(): shutil.copy2(BASE / name, backup / name)
        state.update(version=version['id'], server_port=int(props.get('server-port', 25566)),
                     java_major=version.get('java_version', 8), protocol=version.get('protocol_version', -1),
                     name=props.get('motd', 'Minecraft Server'), gamemode=props.get('gamemode', 'survival'),
                     difficulty=props.get('difficulty', 'easy'), max_players=int(props.get('max-players', 20)),
                     online_mode=props.get('online-mode', 'true') == 'true', complete=True, imported=True)
        # Preserve current RAM from the live Java command, when available.
        try:
            import psutil
            proc = psutil.Process(int((BASE / 'server.pid').read_text()))
            for arg in proc.cmdline():
                m = re.fullmatch(r'-Xmx(\d+)([mMgG])', arg)
                if m: state['memory_mb'] = int(m[1]) * (1024 if m[2].lower() == 'g' else 1)
            state['java'] = proc.exe()
        except Exception: pass
        save_settings(state)
        return state

def install(data, progress):
    with operation_lock():
        state = settings()
        if running(): raise ValueError('Stop Minecraft before changing versions')
        if data.get('eula') is not True: raise ValueError('Accept the Minecraft EULA to continue')
        options = validate_options(data)
        meta = metadata(str(data.get('version', '')))
        if meta['type'] != 'release' and data.get('snapshot_confirm') is not True:
            raise ValueError('Confirm experimental snapshot installation')
        current = installed_version()
        if current and current['id'] != meta['id']:
            # Release timestamps are authoritative; numeric string comparison is not.
            old = metadata(current['id'])
            if meta['releaseTime'] < old['releaseTime']: raise ValueError('In-place downgrades are blocked')
        required = meta.get('javaVersion', {}).get('majorVersion', 8)
        check_java(options['java'], required)
        if current:
            progress('Backing up world', 0); backup_world()
        download = meta['downloads']['server']; validate_url(download['url'])
        target = BASE / 'server.jar'; temp = BASE / 'server.jar.part'
        progress('Downloading', 0); sha = hashlib.sha1(); size = 0
        try:
            with urllib.request.urlopen(download['url'], timeout=30) as response, open(temp, 'wb') as out:
                validate_url(response.url)
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk: break
                    out.write(chunk); sha.update(chunk); size += len(chunk)
                    progress('Downloading', min(99, int(size * 100 / download['size'])))
            if size != download['size'] or sha.hexdigest() != download['sha1']:
                raise ValueError('Download checksum or size mismatch; installed server was not changed')
            progress('Verified download', 100)
            if target.exists(): shutil.copy2(target, BASE / 'server.previous.jar')
            # Snapshot configuration so a failed activation can be rolled back.
            rollback = {name: (BASE / name).read_bytes() if (BASE / name).exists() else None
                        for name in ('server.properties', 'eula.txt')}
            prior = settings()
            try:
                update_properties({'motd': data.get('name', 'Minecraft Server'),
                    'gamemode': data.get('gamemode', 'survival'), 'difficulty': data.get('difficulty', 'easy'),
                    'max-players': data.get('max_players', 20), 'online-mode': str(data.get('online_mode', True)).lower(),
                    'server-ip': '127.0.0.1', 'server-port': options['server_port']})
                (BASE / 'eula.txt').write_text('eula=true\n')
                state.update(options, version=meta['id'], java_major=required,
                             protocol=-1, complete=True, imported=False,
                             **{k: data.get(k, default) for k, default in [('name','Minecraft Server'),
                                ('gamemode','survival'),('difficulty','easy'),('max_players',20),('online_mode',True)]})
                # Bundled version.json is the authoritative game-protocol descriptor.
                with zipfile.ZipFile(temp) as archive:
                    version_info = json.loads(archive.read('version.json')) if 'version.json' in archive.namelist() else {}
                    state['protocol'] = version_info.get('protocol_version', -1)
                save_settings(state)
                os.replace(temp, target)
            except Exception:
                for name, value in rollback.items():
                    if value is None: (BASE / name).unlink(missing_ok=True)
                    else: (BASE / name).write_bytes(value)
                save_settings(prior)
                raise
            return state
        finally: temp.unlink(missing_ok=True)

def preflight():
    state = settings()
    if not state['complete']: raise ValueError('Complete setup at http://127.0.0.1:3000/setup first')
    if not (BASE / 'server.jar').exists(): raise ValueError('Installed server jar is missing')
    try:
        actual = installed_version()
        if actual and actual['id'] != state['version']:
            raise ValueError('Installed jar differs from saved version; repair it using Installation settings')
    except KeyError:
        pass  # Older official jars do not embed version.json.
    if not (BASE / 'eula.txt').exists() or not re.search(r'^eula=true\s*$', (BASE / 'eula.txt').read_text(), re.M):
        raise ValueError('Minecraft EULA acceptance is required')
    check_java(state['java'], state.get('java_major', 8))
    return state

if __name__ == '__main__':
    import sys
    try:
        if sys.argv[1:] == ['preflight']: preflight()
        elif sys.argv[1:] == ['ready']: sys.exit(0 if settings()['complete'] else 1)
        elif sys.argv[1:] == ['token']:
            from dashboard.access import bootstrap_token
            print('First-run setup token: ' + bootstrap_token())
        elif len(sys.argv) == 3 and sys.argv[1] == 'pid':
            pid = process_pid(sys.argv[2])
            if pid: print(pid)
            else: sys.exit(1)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr); sys.exit(1)
