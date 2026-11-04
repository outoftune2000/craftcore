"""Local administrator authentication and server-side expiring sessions."""
import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import re
import uuid
from contextlib import contextmanager
from urllib.parse import urlparse
import tornado.web
from installation import STATE, read_json, write_json, settings
from dashboard.roles import PLAYER_READ_PATHS

AUTH_LOCK = threading.Lock()
SESSIONS = {}
ATTEMPTS = {}
REGISTRATION_ATTEMPTS = {}
SESSION_SECONDS = 8 * 3600
INVITE_SECONDS = 48 * 3600

@contextmanager
def credential_lock():
    import fcntl
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with AUTH_LOCK, open(STATE / 'accounts.lock', 'a+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(stream, fcntl.LOCK_UN)

def player_store():
    store=read_json(STATE / 'players.json', {'accounts': {}})
    store.setdefault('requests',{})
    return store

def list_registrations():
    return [dict(id=key, **{field:value.get(field) for field in ('username','minecraft_name','created_at')})
            for key,value in player_store()['requests'].items()]

def request_player_account(username, minecraft_name, password):
    if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,32}',username):
        raise ValueError('Dashboard username must be 3–32 letters, numbers, dots, underscores, or hyphens')
    if not isinstance(minecraft_name,str) or not re.fullmatch(r'[A-Za-z0-9_]{1,16}',minecraft_name):
        raise ValueError('Enter a valid Minecraft username (1–16 letters, numbers, or underscores)')
    if not isinstance(password,str) or not 12<=len(password)<=256:
        raise ValueError('Use a password of 12–256 characters')
    with credential_lock():
        admin=read_json(STATE/'admin.json')
        if not admin or not settings()['complete']:
            raise ValueError('Player registration opens after the administrator completes server setup')
        store=player_store()
        names=[admin['username'].casefold()]+[v['username'].casefold() for v in store['accounts'].values()]+[v['username'].casefold() for v in store['requests'].values()]
        if username.casefold() in names: raise ValueError('That dashboard username is already in use or awaiting approval')
        if len(store['requests'])>=100: raise ValueError('The approval queue is full. Contact the administrator.')
        request_id=secrets.token_hex(16)
        store['requests'][request_id]={'username':username,'minecraft_name':minecraft_name,
            'created_at':time.time(),**hash_password(password)}
        write_json(STATE/'players.json',store)
    return request_id

def approve_registration(request_id, player_uuid, minecraft_name):
    player_uuid=str(uuid.UUID(player_uuid))
    with credential_lock():
        store=player_store();request=store['requests'].get(request_id)
        if not request: raise ValueError('This request has already been handled or does not exist')
        admin=read_json(STATE/'admin.json')
        if request['username'].casefold()==admin.get('username','').casefold() or any(a['username'].casefold()==request['username'].casefold() for a in store['accounts'].values()):
            raise ValueError('The requested dashboard username is no longer available')
        if any(a['player_uuid']==player_uuid for a in store['accounts'].values()):
            raise ValueError('This Minecraft player already has dashboard access')
        account_id=secrets.token_hex(16)
        store['accounts'][account_id]={
            'username':request['username'],'player_uuid':player_uuid,'minecraft_name':minecraft_name,
            'created_at':time.time(),'credential_version':secrets.token_hex(16),
            'salt':request['salt'],'hash':request['hash']}
        del store['requests'][request_id]
        write_json(STATE/'players.json',store)
        return account_id

def reject_registration(request_id):
    with credential_lock():
        store=player_store()
        if request_id not in store['requests']: raise ValueError('This request has already been handled or does not exist')
        del store['requests'][request_id]
        write_json(STATE/'players.json',store)

def identity_for(account_id, account):
    return {key:account[key] for key in ('username','player_uuid','minecraft_name') if key in account} | {
        'role':'player', 'account_id':account_id, 'credential_version':account['credential_version']}

def admin_identity():
    account = read_json(STATE / 'admin.json')
    return {'role':'admin','username':account.get('username',''), 'credential_version':account.get('credential_version',1)}

def public_identity(identity):
    return {key:identity[key] for key in ('role','username','player_uuid','minecraft_name') if key in identity}

def list_player_accounts():
    return [dict(id=key, **{field:value.get(field) for field in ('username','player_uuid','minecraft_name','created_at')},
                 status='active' if value.get('hash') else 'invited',invite_expires=value.get('invite_expires'))
            for key,value in player_store()['accounts'].items()]

def create_player_account(username, player_uuid, minecraft_name):
    if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,32}',username):
        raise ValueError('Login name must be 3–32 letters, numbers, dots, underscores, or hyphens')
    player_uuid = str(uuid.UUID(player_uuid))
    with credential_lock():
        store = player_store()
        names = [v['username'].casefold() for v in store['accounts'].values()]
        names.extend(v['username'].casefold() for v in store['requests'].values())
        names.append(read_json(STATE/'admin.json').get('username','').casefold())
        if username.casefold() in names: raise ValueError('That dashboard login name is already in use')
        if any(v['player_uuid']==player_uuid for v in store['accounts'].values()):
            raise ValueError('This Minecraft player already has dashboard access; reset its invitation instead')
        account_id = secrets.token_hex(16)
        token = secrets.token_urlsafe(32)
        store['accounts'][account_id] = {'username':username,'player_uuid':player_uuid,
            'minecraft_name':minecraft_name,'created_at':time.time(),'credential_version':secrets.token_hex(16),
            'invite_hash':hashlib.sha256(token.encode()).hexdigest(),'invite_expires':time.time()+INVITE_SECONDS}
        write_json(STATE/'players.json',store)
    return {'id':account_id,'token':token,'username':username}

def reset_player_account(account_id):
    with credential_lock():
        store=player_store();account=store['accounts'].get(account_id)
        if not account: raise ValueError('Dashboard account not found')
        token=secrets.token_urlsafe(32)
        account.update(credential_version=secrets.token_hex(16),invite_hash=hashlib.sha256(token.encode()).hexdigest(),
                       invite_expires=time.time()+INVITE_SECONDS)
        account.pop('hash',None);account.pop('salt',None)
        write_json(STATE/'players.json',store)
        return {'id':account_id,'token':token,'username':account['username']}

def delete_player_account(account_id):
    with credential_lock():
        store=player_store()
        if account_id not in store['accounts']: raise ValueError('Dashboard account not found')
        del store['accounts'][account_id];write_json(STATE/'players.json',store)

def activate_player(token, password):
    if not isinstance(token,str) or not 30 <= len(token) <= 100: raise ValueError('Invitation is invalid or expired')
    if not isinstance(password,str) or not 12 <= len(password) <= 256: raise ValueError('Use a password of 12–256 characters')
    digest=hashlib.sha256(token.encode()).hexdigest()
    with credential_lock():
        store=player_store()
        for key,account in store['accounts'].items():
            if account.get('invite_expires',0)>time.time() and hmac.compare_digest(account.get('invite_hash',''),digest):
                account.update(hash_password(password));account.pop('invite_hash',None);account.pop('invite_expires',None)
                write_json(STATE/'players.json',store)
                return identity_for(key,account)
    raise ValueError('Invitation is invalid or expired')

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
        if any(v['username'].casefold()==username.strip().casefold() for v in player_store()['accounts'].values()):
            raise ValueError('That login name belongs to a player; choose another administrator name')
        expected = read_json(STATE / 'bootstrap.json').get('token', '')
        if not expected or not isinstance(token, str) or not hmac.compare_digest(token, expected):
            raise ValueError('Invalid setup token; read it from the server terminal')
        write_json(STATE / 'admin.json', {'username': username.strip(), 'credential_version':secrets.token_hex(16), **hash_password(password)})
        (STATE / 'bootstrap.json').unlink(missing_ok=True)

def authenticate(username, password):
    admin = read_json(STATE / 'admin.json')
    if not isinstance(username,str) or not isinstance(password,str) or len(password)>256: return False
    if admin and username.strip().casefold()==admin['username'].casefold():
        candidate=hash_password(password,bytes.fromhex(admin['salt']))
        return admin_identity() if hmac.compare_digest(candidate['hash'],admin['hash']) else False
    for key,account in player_store()['accounts'].items():
        if username.strip().casefold()==account['username'].casefold() and account.get('hash'):
            candidate=hash_password(password,bytes.fromhex(account['salt']))
            return identity_for(key,account) if hmac.compare_digest(candidate['hash'],account['hash']) else False
    return False

def session(handler):
    token = handler.get_cookie('craftcore_session', '')
    key = hashlib.sha256(token.encode()).hexdigest()
    entry = SESSIONS.get(key)
    if not entry or entry['expires'] <= time.time():
        SESSIONS.pop(key, None)
        return None
    if entry.get('role')=='admin':
        account=read_json(STATE/'admin.json')
        valid=bool(account) and entry.get('username')==account.get('username') and entry.get('credential_version')==account.get('credential_version',1)
    elif entry.get('role')=='player':
        account=player_store()['accounts'].get(entry.get('account_id'))
        valid=bool(account and account.get('hash')) and entry.get('credential_version')==account.get('credential_version') and entry.get('player_uuid')==account.get('player_uuid')
    else: valid=False
    if not valid:
        SESSIONS.pop(key,None);return None
    return entry

def new_session(handler, identity):
    token = secrets.token_urlsafe(32)
    # Bound memory and discard expired sessions.
    now = time.time()
    for key, entry in list(SESSIONS.items()):
        if entry['expires'] <= now: SESSIONS.pop(key, None)
    if len(SESSIONS) >= 100: SESSIONS.pop(next(iter(SESSIONS)))
    SESSIONS[hashlib.sha256(token.encode()).hexdigest()] = {**identity,'expires': now + SESSION_SECONDS}
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
        identity=session(self)
        if not identity: raise tornado.web.HTTPError(401, 'Sign in required')
        if identity['role']!='admin' and (self.request.method!='GET' or self.request.path not in PLAYER_READ_PATHS):
            raise tornado.web.HTTPError(403,'Administrator access required')
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
        identity=session(self)
        self.write({'admin_exists': (STATE / 'admin.json').exists(), 'authenticated': bool(session(self)),
                    'complete': settings()['complete'],'registration_open':(STATE/'admin.json').exists() and settings()['complete'],'user':public_identity(identity) if identity else None})

def throttle(handler):
    key = handler.request.remote_ip
    recent = [stamp for stamp in ATTEMPTS.get(key, []) if stamp > time.time() - 300]
    if len(recent) >= 10: raise tornado.web.HTTPError(429, 'Too many attempts; retry in five minutes')
    ATTEMPTS[key] = recent + [time.time()]

class RegisterHandler(PublicHandler):
    async def post(self):
        key=self.request.remote_ip;now=time.time()
        recent=[t for t in REGISTRATION_ATTEMPTS.get(key,[]) if t>now-3600]
        if len(recent)>=3:
            self.set_status(429);self.write({'error':'Too many registration attempts. Retry in one hour or contact the administrator.'});return
        REGISTRATION_ATTEMPTS[key]=recent+[now]
        data=self.body_json()
        try:
            await asyncio.to_thread(request_player_account,data.get('username'),data.get('minecraft_name'),data.get('password'))
        except ValueError as exc:
            self.set_status(400);self.write({'error':str(exc)});return
        self.set_status(202)
        self.write({'success':True,'status':'pending','message':'Request submitted. An administrator must approve your player account before you can sign in.'})

class LoginHandler(PublicHandler):
    async def post(self):
        throttle(self); data = self.body_json()
        identity=await asyncio.to_thread(authenticate, data.get('username'), data.get('password'))
        if not identity:
            raise tornado.web.HTTPError(401, 'Invalid login')
        new_session(self,identity); self.write({'success': True,'user':public_identity(identity)})

class AdminHandler(PublicHandler):
    async def post(self):
        throttle(self); data = self.body_json()
        try:
            await asyncio.to_thread(create_admin, data.get('token'), data.get('username'), data.get('password'))
        except ValueError as exc: raise tornado.web.HTTPError(400, str(exc))
        new_session(self,admin_identity()); self.write({'success': True})

class ActivateHandler(PublicHandler):
    async def post(self):
        throttle(self);data=self.body_json()
        try: identity=await asyncio.to_thread(activate_player,data.get('token'),data.get('password'))
        except ValueError as exc: raise tornado.web.HTTPError(400,str(exc))
        new_session(self,identity);self.write({'success':True,'user':public_identity(identity)})

class LogoutHandler(PublicHandler):
    def post(self):
        token = self.get_cookie('craftcore_session', '')
        SESSIONS.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        self.clear_cookie('craftcore_session')
        self.write({'success': True})

def same_origin(handler, origin):
    value = urlparse(origin)
    return value.netloc == handler.request.host and value.scheme == handler.request.protocol
