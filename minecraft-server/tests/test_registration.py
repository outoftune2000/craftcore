import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from tornado.testing import AsyncHTTPTestCase
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import installation as ins
from dashboard import access,server,setup_api

A='11111111-1111-4111-8111-111111111111'
B='22222222-2222-4222-8222-222222222222'
PASSWORD='registration-test-password'

class RegistrationTests(AsyncHTTPTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.patches=[patch.object(ins,'BASE',self.base),patch.object(ins,'STATE',self.base/'.state'),
                      patch.object(access,'STATE',self.base/'.state'),patch.object(setup_api,'STATE',self.base/'.state')]
        for p in self.patches:p.start()
        access.SESSIONS.clear();access.ATTEMPTS.clear();access.REGISTRATION_ATTEMPTS.clear()
        access.create_admin(access.bootstrap_token(),'admin','administrator-test-password')
        ins.save_settings(ins.settings()|{'complete':True})
        super().setUp()
    def tearDown(self):
        super().tearDown()
        for p in self.patches:p.stop()
        self.temp.cleanup()
    def get_app(self):return server.make_app()
    def headers(self,identity=None):
        response=self.fetch('/api/auth/status')
        cookie=next(value.split(';')[0] for value in response.headers.get_list('Set-Cookie') if value.startswith('_xsrf='))
        if identity:
            token='registration-session-'+str(time.time_ns())
            access.SESSIONS[hashlib.sha256(token.encode()).hexdigest()]={**identity,'expires':time.time()+100}
            cookie+='; craftcore_session='+token
        return {'Cookie':cookie,'X-XSRFToken':cookie.split(';')[0].split('=',1)[1]}
    def submit(self,username='blair',**extra):
        data={'username':username,'minecraft_name':'Blair','password':PASSWORD,**extra}
        return self.fetch('/api/auth/register',method='POST',headers=self.headers(),body=json.dumps(data))
    def review(self,request_id,action='approve',player_uuid=A,identity=None):
        return self.fetch('/api/accounts',method='POST',headers=self.headers(identity or access.admin_identity()),body=json.dumps({'id':request_id,'action':action,'player_uuid':player_uuid}))
    def test_pending_request_has_no_account_session_or_login(self):
        response=self.submit();self.assertEqual(response.code,202,response.body)
        self.assertEqual(json.loads(response.body)['status'],'pending')
        self.assertFalse(any('craftcore_session=' in value for value in response.headers.get_list('Set-Cookie')))
        store=access.player_store();self.assertEqual(store['accounts'],{});self.assertEqual(len(store['requests']),1)
        self.assertNotIn(PASSWORD,(access.STATE/'players.json').read_text())
        self.assertFalse(access.authenticate('blair',PASSWORD))
        self.assertEqual(self.fetch('/api/me',headers=self.headers()).code,401)
    def test_admin_approval_creates_login_with_requested_password(self):
        self.submit();request_id=access.list_registrations()[0]['id']
        with patch.object(server,'known_dashboard_players',return_value=[{'uuid':A,'name':'VerifiedBlair'}]):
            response=self.review(request_id);self.assertEqual(response.code,200,response.body)
        identity=access.authenticate('blair',PASSWORD);self.assertEqual(identity['role'],'player');self.assertEqual(identity['player_uuid'],A)
        self.assertEqual(identity['minecraft_name'],'VerifiedBlair');self.assertEqual(access.list_registrations(),[])
        self.assertEqual(len(access.player_store()['accounts']),1)
    def test_rejection_never_creates_an_account(self):
        self.submit();request_id=access.list_registrations()[0]['id']
        self.assertEqual(self.review(request_id,'reject').code,200)
        self.assertEqual(access.player_store()['accounts'],{});self.assertEqual(access.list_registrations(),[])
        self.assertFalse(access.authenticate('blair',PASSWORD))
    def test_unverified_uuid_cannot_be_approved(self):
        self.submit();request_id=access.list_registrations()[0]['id']
        with patch.object(server,'known_dashboard_players',return_value=[]):self.assertEqual(self.review(request_id).code,400)
        self.assertFalse(access.authenticate('blair',PASSWORD));self.assertTrue(access.list_registrations())
    def test_role_and_uuid_claims_are_ignored(self):
        self.assertEqual(self.submit(role='admin',player_uuid=B).code,202)
        request=next(iter(access.player_store()['requests'].values()))
        self.assertNotIn('role',request);self.assertNotIn('player_uuid',request)
        self.assertEqual(access.player_store()['accounts'],{})
    def test_credentials_not_exposed_in_admin_queue(self):
        self.submit()
        data=json.loads(self.fetch('/api/accounts',headers=self.headers(access.admin_identity())).body)
        self.assertEqual(len(data['registrations']),1)
        self.assertNotIn('hash',data['registrations'][0]);self.assertNotIn('salt',data['registrations'][0]);self.assertNotIn(PASSWORD,str(data))
        self.assertEqual(self.fetch('/api/accounts').code,401)
    def test_player_cannot_approve_or_reject_requests(self):
        self.submit();request_id=access.list_registrations()[0]['id']
        invitation=access.create_player_account('existing',A,'Existing')
        identity=access.activate_player(invitation['token'],'existing-test-password')
        for action in ('approve','reject'):self.assertEqual(self.review(request_id,action,B,identity).code,403)
        self.assertTrue(access.list_registrations())
    def test_admin_and_duplicate_names_are_reserved(self):
        self.assertEqual(self.submit('ADMIN').code,400)
        self.assertEqual(self.submit().code,202);self.assertEqual(self.submit('BLAIR').code,400)
        with self.assertRaises(ValueError):access.create_player_account('blair',A,'Blair')
    def test_csrf_and_rate_limit(self):
        self.assertEqual(self.fetch('/api/auth/register',method='POST',body='{}').code,403)
        for i in range(3):self.assertEqual(self.submit('request'+str(i)).code,202)
        self.assertEqual(self.submit('request4').code,429)
        self.assertEqual(len(access.list_registrations()),3)
    def test_registration_closed_before_setup_or_admin(self):
        ins.save_settings(ins.settings()|{'complete':False});self.assertEqual(self.submit().code,400)
        ins.save_settings(ins.settings()|{'complete':True});(access.STATE/'admin.json').unlink()
        self.assertEqual(self.submit().code,400);self.assertEqual(access.player_store()['accounts'],{})
    def test_approval_is_atomic_and_single_use(self):
        request_id=access.request_player_account('blair','Blair',PASSWORD);results=[]
        def approve():
            try:access.approve_registration(request_id,A,'Blair');results.append(True)
            except ValueError:results.append(False)
        threads=[threading.Thread(target=approve) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(sum(results),1);self.assertEqual(len(access.player_store()['accounts']),1)
        with self.assertRaises(ValueError):access.reject_registration(request_id)
    def test_existing_player_uuid_cannot_be_claimed_twice(self):
        access.create_player_account('existing',A,'Existing')
        request_id=access.request_player_account('blair','Blair',PASSWORD)
        with self.assertRaises(ValueError):access.approve_registration(request_id,A,'Blair')
        self.assertEqual(len(access.player_store()['accounts']),1);self.assertTrue(access.list_registrations())
    def test_public_registration_assets(self):
        for path in ('/register','/register.js'):
            response=self.fetch(path);self.assertEqual(response.code,200)
        response=self.fetch('/login');self.assertIn(b'Create account',response.body)
    def test_invalid_registration_is_not_saved(self):
        self.assertEqual(self.submit(password='short').code,400)
        self.assertEqual(self.submit(minecraft_name='invalid name').code,400)
        self.assertEqual(access.list_registrations(),[])

if __name__=='__main__':unittest.main()
