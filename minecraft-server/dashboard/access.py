"""Local administrator authentication and server-side expiring sessions."""
import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from urllib.parse import urlparse
import tornado.web
from installation import STATE, read_json, write_json, settings

AUTH_LOCK = threading.Lock()
SESSIONS = {}
ATTEMPTS = {}
SESSION_SECONDS = 8 * 3600

def bootstrap_token():
    with AUTH_LOCK:
        if (STATE / 'admin.json').exists(): return '(administrator already configured)'
        value = read_json(STATE / 'bootstrap.json')
        if not value:
            value = {'token': secrets.token_urlsafe(32)}
            write_json(STATE / 'bootstrap.json', value)
        return value['token']

def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return {'salt': salt.hex(), 'hash': digest.hex()}

def create_admin(token, username, password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('Use a password of 12–256 characters')
    if not isinstance(username, str) or not 1 <= len(username.strip()) <= 64:
        raise ValueError('Choose an administrator name (1–64 characters)')
    # Filesystem exclusion protects against two dashboard processes claiming setup.
    from installation import operation_lock
    with operation_lock(), AUTH_LOCK:
        if (STATE / 'admin.json').exists(): raise ValueError('Administrator is already configured')
        expected = read_json(STATE / 'bootstrap.json').get('token', '')
        if not expected or not isinstance(token, str) or not hmac.compare_digest(token, expected):
            raise ValueError('Invalid setup token; read it from the server terminal')
        write_json(STATE / 'admin.json', {'username': username.strip(), **hash_password(password)})
        (STATE / 'bootstrap.json').unlink(missing_ok=True)

def authenticate(username, password):
    admin = read_json(STATE / 'admin.json')
    if not admin or not isinstance(password, str) or len(password) > 256: return False
    candidate = hash_password(password, bytes.fromhex(admin['salt']))
    return hmac.compare_digest(candidate['hash'], admin['hash']) and username == admin['username']

def session(handler):
    token = handler.get_cookie('craftcore_session', '')
    key = hashlib.sha256(token.encode()).hexdigest()
    entry = SESSIONS.get(key)
    if not entry or entry['expires'] <= time.time():
        SESSIONS.pop(key, None)
        return None
    return entry

def new_session(handler):
    token = secrets.token_urlsafe(32)
    # Bound memory and discard expired sessions.
    now = time.time()
    for key, entry in list(SESSIONS.items()):
        if entry['expires'] <= now: SESSIONS.pop(key, None)
    if len(SESSIONS) >= 100: SESSIONS.pop(next(iter(SESSIONS)))
    SESSIONS[hashlib.sha256(token.encode()).hexdigest()] = {'expires': now + SESSION_SECONDS}
    handler.set_cookie('craftcore_session', token, httponly=True, samesite='Strict',
                       secure=handler.request.protocol == 'https', max_age=SESSION_SECONDS)

class ProtectedHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('X-Content-Type-Options', 'nosniff')
        self.set_header('X-Frame-Options', 'DENY')
        self.set_header('Referrer-Policy', 'no-referrer')
        self.set_header('Cache-Control', 'no-store')

    def prepare(self):
        self.xsrf_token
        if not session(self): raise tornado.web.HTTPError(401, 'Sign in required')
        if not settings()['complete']: raise tornado.web.HTTPError(409, 'Complete first-run setup')

class PublicHandler(ProtectedHandler):
    def prepare(self):
        self.xsrf_token

    def body_json(self):
        try:
            value = json.loads(self.request.body)
            if not isinstance(value, dict): raise ValueError()
            return value
        except (ValueError, TypeError): raise tornado.web.HTTPError(400, 'Expected a JSON object')

class AuthStatusHandler(PublicHandler):
    def get(self):
        self.write({'admin_exists': (STATE / 'admin.json').exists(), 'authenticated': bool(session(self)),
                    'complete': settings()['complete']})

def throttle(handler):
    key = handler.request.remote_ip
    recent = [stamp for stamp in ATTEMPTS.get(key, []) if stamp > time.time() - 300]
    if len(recent) >= 10: raise tornado.web.HTTPError(429, 'Too many attempts; retry in five minutes')
    ATTEMPTS[key] = recent + [time.time()]

class LoginHandler(PublicHandler):
    async def post(self):
        throttle(self); data = self.body_json()
        if not await asyncio.to_thread(authenticate, data.get('username'), data.get('password')):
            raise tornado.web.HTTPError(401, 'Invalid login')
        new_session(self); self.write({'success': True})

class AdminHandler(PublicHandler):
    async def post(self):
        throttle(self); data = self.body_json()
        try:
            await asyncio.to_thread(create_admin, data.get('token'), data.get('username'), data.get('password'))
        except ValueError as exc: raise tornado.web.HTTPError(400, str(exc))
        new_session(self); self.write({'success': True})

class LogoutHandler(PublicHandler):
    def post(self):
        token = self.get_cookie('craftcore_session', '')
        SESSIONS.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        self.clear_cookie('craftcore_session')
        self.write({'success': True})

def same_origin(handler, origin):
    value = urlparse(origin)
    return value.netloc == handler.request.host and value.scheme == handler.request.protocol
