import asyncio
import threading
import time
import subprocess
import tornado.web
from installation import (STATE, BASE, settings, read_json, write_json, installed_version,
    available_versions, metadata, java_version, validate_options, install, import_existing, running)
from dashboard.access import PublicHandler, session

JOB_LOCK = threading.Lock()
JOB = {'state': 'idle', 'message': '', 'percent': 0}
CATALOG_LOCK = asyncio.Lock()

class SetupHandler(PublicHandler):
    def prepare(self):
        super().prepare()
        if not session(self): raise tornado.web.HTTPError(401, 'Sign in required')
        if session(self)['role']!='admin': raise tornado.web.HTTPError(403, 'Administrator access required')

class SetupStatusHandler(SetupHandler):
    async def get(self):
        try: version = await asyncio.to_thread(installed_version)
        except Exception: version = None
        self.write({'settings': settings(), 'draft': read_json(STATE / 'draft.json'),
                    'existing_version': version, 'running': running(), 'job': JOB.copy()})

class DraftHandler(SetupHandler):
    def post(self):
        data = self.body_json()
        # Never persist passwords, setup tokens, or arbitrary browser data.
        allowed = {'step','version','java','memory_mb','proxy_port','server_port','name',
                   'gamemode','difficulty','max_players','online_mode','snapshot_confirm','offline_confirm','eula'}
        write_json(STATE / 'draft.json', {k: v for k, v in data.items() if k in allowed})
        self.write({'success': True})

class VersionsHandler(SetupHandler):
    async def get(self):
        try:
            async with CATALOG_LOCK:
                result = await asyncio.to_thread(available_versions,
                    self.get_argument('snapshots', '0') == '1', self.get_argument('refresh', '0') == '1',
                    int(self.get_argument('offset', '0')))
            self.write(result)
        except (ValueError, OSError) as exc: raise tornado.web.HTTPError(503, str(exc))

class JavaHandler(SetupHandler):
    async def post(self):
        try:
            major = await asyncio.to_thread(java_version, self.body_json().get('java', 'java'))
            self.write({'major': major})
        except ValueError as exc: raise tornado.web.HTTPError(400, str(exc))

def update_job(message, percent):
    JOB.update(message=message, percent=percent)

def run_install(data):
    try:
        state = install(data, update_job)
        if data.get('start') is True:
            update_job('Starting Minecraft', 100)
            # install has released its exclusive operation lock.
            proc = subprocess.run([str(BASE / 'server.sh'), 'start'], cwd=BASE, capture_output=True, text=True, timeout=120)
            if proc.returncode: raise ValueError('Installed successfully, but startup failed. Check Java and server logs.')
            deadline = time.time() + 120
            log = BASE / 'logs' / 'latest.log'
            while time.time() < deadline:
                if not running(): raise ValueError('Installed successfully, but Minecraft exited. Check server logs.')
                if log.exists() and 'Done (' in log.read_text(errors='replace')[-12000:]: break
                time.sleep(1)
            else: raise ValueError('Installed successfully; startup has not completed within two minutes. Check logs.')
        JOB.update(state='done', message='Setup complete', percent=100)
    except Exception as exc:
        JOB.update(state='error', message=str(exc), percent=0)
    finally: JOB_LOCK.release()

class InstallHandler(SetupHandler):
    def post(self):
        data = self.body_json()
        if not JOB_LOCK.acquire(False): raise tornado.web.HTTPError(409, 'Installation already in progress')
        JOB.update(state='running', message='Checking installation', percent=0)
        threading.Thread(target=run_install, args=(data,), daemon=True).start()
        self.set_status(202); self.write(JOB.copy())

class ImportHandler(SetupHandler):
    async def post(self):
        try: result = await asyncio.to_thread(import_existing)
        except (ValueError, OSError) as exc: raise tornado.web.HTTPError(400, str(exc))
        self.write({'success': True, 'settings': result})

def routes():
    return [(r'/api/setup/status', SetupStatusHandler), (r'/api/setup/draft', DraftHandler),
            (r'/api/versions', VersionsHandler), (r'/api/setup/java', JavaHandler),
            (r'/api/setup/install', InstallHandler), (r'/api/setup/import', ImportHandler)]
