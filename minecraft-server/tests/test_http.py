import hashlib
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from tornado.testing import AsyncHTTPTestCase, gen_test
from tornado.httpclient import HTTPRequest, HTTPClientError
from tornado.websocket import websocket_connect
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import installation as ins
from dashboard import access, server, setup_api

class HTTPTests(AsyncHTTPTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.patches=[patch.object(ins,'BASE',self.base),patch.object(ins,'STATE',self.base/'.state'),patch.object(access,'STATE',self.base/'.state'),patch.object(setup_api,'STATE',self.base/'.state')]
        for p in self.patches:p.start()
        access.SESSIONS.clear();access.ATTEMPTS.clear()
        self.token=access.bootstrap_token()
        super().setUp()
    def tearDown(self):
        super().tearDown()
        for p in self.patches:p.stop()
        self.temp.cleanup()
    def get_app(self):return server.make_app()
    def csrf(self):
        response=self.fetch('/api/auth/status')
        cookie=response.headers.get_list('Set-Cookie')[0].split(';')[0]
        return cookie,cookie.split('=',1)[1]
    def claim(self):
        cookie,csrf=self.csrf()
        response=self.fetch('/api/setup/admin',method='POST',headers={'Cookie':cookie,'X-XSRFToken':csrf},body=json.dumps({'token':self.token,'username':'admin','password':'long-test-password'}))
        self.assertEqual(response.code,200,response.body)
        session=response.headers.get_list('Set-Cookie')[0].split(';')[0]
        return cookie+'; '+session,csrf
    def test_private_endpoints_and_setup_page(self):
        for path in ['/api/status','/api/config','/api/setup/status','/api/versions','/api/map/terrain']:
            self.assertEqual(self.fetch(path).code,401)
        response=self.fetch('/setup');self.assertEqual(response.code,200,response.body)
        self.assertIn(b'Your world starts here',response.body)
    def test_map_dimension_aliases(self):
        cookie,csrf=self.claim();ins.save_settings(ins.settings()|{'complete':True})
        with patch.object(server,'mesh_tile',return_value={'heights':[],'colors':[]}) as mesh:
            response=self.fetch('/api/map/terrain?dim=nether&rx=0&rz=0',headers={'Cookie':cookie})
            self.assertEqual(response.code,200)
            self.assertEqual(mesh.call_args.args[2],'the_nether')
        response=self.fetch('/api/map/terrain?dim=invalid',headers={'Cookie':cookie})
        self.assertEqual(response.code,400)

    def test_map_invalid_detail_rejected(self):
        cookie,csrf=self.claim();ins.save_settings(ins.settings()|{'complete':True})
        response=self.fetch('/api/map/terrain?dim=overworld&step=7',headers={'Cookie':cookie})
        self.assertEqual(response.code,400)

    def test_csrf_required_for_claim(self):
        response=self.fetch('/api/setup/admin',method='POST',body='{}')
        self.assertEqual(response.code,403)
    def test_resume_draft_and_single_claim(self):
        cookie,csrf=self.claim();headers={'Cookie':cookie,'X-XSRFToken':csrf}
        self.assertEqual(self.fetch('/api/setup/draft',method='POST',headers=headers,body=json.dumps({'step':3,'name':'Saved','password':'must-not-save'})).code,200)
        value=json.loads(self.fetch('/api/setup/status',headers=headers).body)
        self.assertEqual(value['draft']['name'],'Saved');self.assertNotIn('password',value['draft'])
        self.assertEqual(self.fetch('/api/setup/admin',method='POST',headers=headers,body=json.dumps({'token':self.token,'username':'a','password':'long-test-password'})).code,400)
    def test_expiry_and_logout(self):
        cookie,csrf=self.claim()
        for entry in access.SESSIONS.values():entry['expires']=time.time()-1
        self.assertEqual(self.fetch('/api/setup/status',headers={'Cookie':cookie}).code,401)
    def test_logout_revokes_session(self):
        cookie,csrf=self.claim()
        self.assertEqual(self.fetch('/api/auth/logout',method='POST',headers={'Cookie':cookie,'X-XSRFToken':csrf},body='{}').code,200)
        self.assertEqual(self.fetch('/api/setup/status',headers={'Cookie':cookie}).code,401)
    def test_secret_redaction_and_csrf(self):
        cookie,csrf=self.claim();ins.save_settings(ins.settings()|{'complete':True})
        with patch.object(server,'read_properties',return_value={'management-server-secret':'hidden','rcon.password':'hidden','motd':'public'}):
            value=json.loads(self.fetch('/api/config',headers={'Cookie':cookie}).body)
            self.assertEqual(value,{'motd':'public','players-sleeping-percentage':'50'})
        self.assertEqual(self.fetch('/api/cmd',method='POST',headers={'Cookie':cookie},body='{}').code,403)
    @gen_test
    async def test_backup_does_not_block_http(self):
        response=await self.http_client.fetch(self.get_url('/api/auth/status'))
        cookie=response.headers.get_list('Set-Cookie')[0].split(';')[0];csrf=cookie.split('=',1)[1]
        access.create_admin(self.token,'admin','long-test-password')
        token='test-background-session';access.SESSIONS[hashlib.sha256(token.encode()).hexdigest()]={**access.admin_identity(),'expires':time.time()+100}
        ins.save_settings(ins.settings()|{'complete':True})
        def slow_backup(**kwargs):time.sleep(.3);return True,'test.zip'
        with patch.object(server,'perform_world_backup',side_effect=slow_backup):
            pending=self.http_client.fetch(self.get_url('/api/backups/create'),method='POST',headers={'Cookie':cookie+'; craftcore_session='+token,'X-XSRFToken':csrf},body='{}')
            await asyncio.sleep(.03);start=time.monotonic()
            await self.http_client.fetch(self.get_url('/api/auth/status'))
            self.assertLess(time.monotonic()-start,.15)
            self.assertEqual((await pending).code,200)
    @gen_test
    async def test_websocket_requires_auth_and_origin(self):
        url=self.get_url('/ws/live').replace('http','ws',1)
        with self.assertRaises(HTTPClientError):await websocket_connect(HTTPRequest(url,headers={'Origin':self.get_url('/').rstrip('/')}))
        # A session alone does not permit cross-origin or missing-Origin requests.
        access.create_admin(self.token,'admin','long-test-password')
        token='testsession';access.SESSIONS[hashlib.sha256(token.encode()).hexdigest()]={**access.admin_identity(),'expires':time.time()+100}
        ins.save_settings(ins.settings()|{'complete':True})
        with self.assertRaises(HTTPClientError):await websocket_connect(HTTPRequest(url,headers={'Cookie':'craftcore_session='+token,'Origin':'http://evil.invalid'}))

if __name__=='__main__':unittest.main()
