import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch
from tornado.testing import AsyncHTTPTestCase,gen_test
from tornado.httpclient import HTTPRequest
from tornado.websocket import websocket_connect
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import installation as ins
from dashboard import access,server,setup_api
from dashboard.roles import player_metrics,map_for

A='11111111-1111-4111-8111-111111111111'
B='22222222-2222-4222-8222-222222222222'

class PlayerHTTPTests(AsyncHTTPTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.patches=[patch.object(ins,'BASE',self.base),patch.object(ins,'STATE',self.base/'.state'),
                      patch.object(access,'STATE',self.base/'.state'),patch.object(setup_api,'STATE',self.base/'.state')]
        for p in self.patches:p.start()
        access.SESSIONS.clear();access.ATTEMPTS.clear()
        access.create_admin(access.bootstrap_token(),'admin','admin-test-password')
        self.invitation=access.create_player_account('alex',A,'Alex')
        self.identity=access.activate_player(self.invitation['token'],'player-test-password')
        ins.save_settings(ins.settings()|{'complete':True})
        super().setUp()
    def tearDown(self):
        super().tearDown()
        for p in self.patches:p.stop()
        self.temp.cleanup()
    def get_app(self):return server.make_app()
    def cookies(self,identity=None):
        response=self.fetch('/api/auth/status')
        xsrf=next(c.split(';')[0] for c in response.headers.get_list('Set-Cookie') if c.startswith('_xsrf='))
        token='test-'+str(time.time_ns())
        access.SESSIONS[hashlib.sha256(token.encode()).hexdigest()]={**(identity or self.identity),'expires':time.time()+100}
        return {'Cookie':xsrf+'; craftcore_session='+token,'X-XSRFToken':xsrf.split('=',1)[1]}
    def test_player_cannot_use_admin_routes(self):
        headers=self.cookies()
        for path in ['/api/config','/api/players','/api/backups','/api/world/info','/api/world/export','/api/security/status','/api/setup/status','/api/versions','/api/accounts']:
            self.assertEqual(self.fetch(path,headers=headers).code,403,path)
        for path in ['/api/action','/api/cmd','/api/config','/api/backups/create','/api/map/waypoints','/api/accounts','/api/setup/install','/api/setup/draft','/api/setup/import','/api/setup/java']:
            self.assertEqual(self.fetch(path,method='POST',headers=headers,body='{}').code,403,path)
    def test_own_inventory_only(self):
        headers=self.cookies()
        with patch.object(server,'get_player_inventory_details',return_value={'uuid':A,'health':17}) as details:
            response=self.fetch('/api/players/inventory?uuid='+A,headers=headers)
            self.assertEqual(response.code,200);self.assertEqual(json.loads(response.body)['player']['uuid'],A)
            details.reset_mock()
            self.assertEqual(self.fetch('/api/players/inventory?uuid='+B,headers=headers).code,403)
            details.assert_not_called()
            with patch.object(server,'known_dashboard_players',return_value=[{'uuid':B,'name':'Other'}]):
                self.assertEqual(self.fetch('/api/players/inventory?name=Other',headers=headers).code,403)
    def test_me_cannot_be_retargeted(self):
        with patch.object(server,'get_player_inventory_details',return_value={'uuid':A}) as details:
            response=self.fetch('/api/me?uuid='+B,headers=self.cookies())
            self.assertEqual(response.code,200);details.assert_called_once_with(A)
    def test_status_and_map_redaction(self):
        headers=self.cookies()
        metrics={'server_running':True,'security':{'admin_passcode':'private','requests_log':['private']},'server_pid':123,'online_players':[{'name':'Other','health':4,'food':2,'uuid':B}],'world':{'name':'world','backups':['private']}}
        with patch.object(server,'get_server_metrics',return_value=metrics):
            data=json.loads(self.fetch('/api/status',headers=headers).body)
            self.assertNotIn('security',data);self.assertNotIn('server_pid',data)
            self.assertEqual(data['online_players'],[{'name':'Other'}]);self.assertNotIn('backups',data['world'])
        with patch.object(server,'get_map_telemetry_data',return_value={'players':[{'name':'Other','uuid':B,'health':4,'food':2,'xp_level':9,'pos':{'x':1},'dimension':'overworld'}]}):
            data=json.loads(self.fetch('/api/map/data',headers=headers).body)
            self.assertNotIn('health',data['players'][0]);self.assertNotIn('xp_level',data['players'][0]);self.assertIn('pos',data['players'][0])
    def test_player_gets_separate_document_and_setup_redirect(self):
        headers=self.cookies();response=self.fetch('/',headers=headers)
        self.assertIn(b'Player dashboard',response.body)
        self.assertNotIn(b'btn-start',response.body);self.assertNotIn(b'tab-console',response.body)
        self.assertEqual(self.fetch('/setup',headers=headers,follow_redirects=False).headers.get('Location'),'/')
    def test_reset_and_revoke_invalidate_existing_sessions(self):
        headers=self.cookies();access.reset_player_account(self.identity['account_id'])
        self.assertEqual(self.fetch('/api/me',headers=headers).code,401)
        self.assertFalse(access.authenticate('alex','player-test-password'))
        invitation=access.reset_player_account(self.identity['account_id'])
        self.identity=access.activate_player(invitation['token'],'replacement-test-password')
        headers=self.cookies();access.delete_player_account(self.identity['account_id'])
        self.assertEqual(self.fetch('/api/me',headers=headers).code,401)
    def test_admin_invites_only_saved_players_and_can_manage(self):
        headers=self.cookies(access.admin_identity())
        with patch.object(server,'known_dashboard_players',return_value=[{'uuid':B,'name':'Blair'}]):
            response=self.fetch('/api/accounts',method='POST',headers=headers,body=json.dumps({'username':'blair','player_uuid':B,'role':'admin'}))
            self.assertEqual(response.code,200);token=json.loads(response.body)['token']
            identity=access.activate_player(token,'blair-test-password');self.assertEqual(identity['role'],'player')
            response=self.fetch('/api/accounts',method='POST',headers=headers,body=json.dumps({'username':'bad','player_uuid':'33333333-3333-4333-8333-333333333333'}))
            self.assertEqual(response.code,400)
        data=json.loads(self.fetch('/api/accounts',headers=headers).body)
        self.assertTrue(data['accounts']);self.assertNotIn('hash',str(data));self.assertNotIn('invite_hash',str(data))
    def test_invitation_expiry_reuse_and_duplicate_bindings(self):
        with self.assertRaises(ValueError):access.activate_player(self.invitation['token'],'another-test-password')
        with self.assertRaises(ValueError):access.create_player_account('admin',B,'Blair')
        with self.assertRaises(ValueError):access.create_player_account('another',A,'Alex')
        invitation=access.reset_player_account(self.identity['account_id']);store=access.player_store()
        store['accounts'][self.identity['account_id']]['invite_expires']=0;ins.write_json(access.STATE/'players.json',store)
        with self.assertRaises(ValueError):access.activate_player(invitation['token'],'new-test-password')
        self.assertNotIn(invitation['token'],(access.STATE/'players.json').read_text())
    def test_credentials_are_hashed_and_player_role_is_fixed(self):
        self.assertNotIn('player-test-password',(access.STATE/'players.json').read_text())
        self.assertEqual(access.authenticate('ALEX','player-test-password')['role'],'player')
        self.assertFalse(access.authenticate('alex','wrong'))
    def test_activation_requires_csrf(self):
        response=self.fetch('/api/auth/activate',method='POST',body='{}')
        self.assertEqual(response.code,403)
    def test_parallel_activation_has_one_winner(self):
        invitation=access.create_player_account('blair',B,'Blair');results=[]
        def claim():
            try:access.activate_player(invitation['token'],'another-test-password');results.append(True)
            except ValueError:results.append(False)
        threads=[threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(sum(results),1)
    def test_activation_body_cannot_grant_admin(self):
        invitation=access.create_player_account('blair',B,'Blair')
        response=self.fetch('/api/auth/activate',method='POST',headers=self.cookies(),body=json.dumps({'token':invitation['token'],'password':'another-test-password','role':'admin','player_uuid':A}))
        self.assertEqual(response.code,200);identity=json.loads(response.body)['user']
        self.assertEqual(identity['role'],'player');self.assertEqual(identity['player_uuid'],B)
    def test_legacy_admin_credentials_continue_to_work(self):
        admin=ins.read_json(access.STATE/'admin.json');admin.pop('credential_version',None);ins.write_json(access.STATE/'admin.json',admin)
        identity=access.authenticate('admin','admin-test-password');self.assertEqual(identity['role'],'admin')
        self.assertEqual(self.fetch('/api/accounts',headers=self.cookies(identity)).code,200)
    def test_path_traversal_inventory_identifiers_rejected(self):
        with patch.object(server,'USERCACHE_FILE',str(self.base/'missing.json')),patch.object(server.gzip,'open') as opened:
            self.assertIsNone(server.get_player_inventory_details('../'*12+'admin-secret'))
            opened.assert_not_called()
    @gen_test
    async def test_player_websocket_has_no_logs_or_commands(self):
        token='player-websocket-test';access.SESSIONS[hashlib.sha256(token.encode()).hexdigest()]={**self.identity,'expires':time.time()+100}
        request=HTTPRequest(self.get_url('/ws/live').replace('http','ws',1),headers={'Cookie':'craftcore_session='+token,'Origin':self.get_url('/').rstrip('/')})
        log=self.base/'private.log';log.write_text('private administrator console output\n')
        with patch.object(server,'get_server_metrics',return_value={'security':{'private':True},'server_running':True}),patch.object(server,'LOG_FILE',str(log)),patch.object(server,'send_server_cmd') as command:
            socket=await websocket_connect(request)
            first=json.loads(await socket.read_message());self.assertEqual(first['type'],'metrics');self.assertNotIn('security',first['data'])
            socket.write_message(json.dumps({'type':'cmd','cmd':'op intruder'}))
            message=json.loads(await socket.read_message());self.assertEqual(message['type'],'error');command.assert_not_called()
            server.broadcast_logs();socket.write_message(json.dumps({'type':'cmd','cmd':'stop'}))
            self.assertEqual(json.loads(await socket.read_message())['type'],'error');command.assert_not_called()
            access.delete_player_account(self.identity['account_id']);socket.write_message(json.dumps({'type':'cmd','cmd':'stop'}))
            self.assertIsNone(await socket.read_message());socket.close()

if __name__=='__main__':unittest.main()
