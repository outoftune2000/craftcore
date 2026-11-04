import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import installation as ins
from dashboard import access
from terrain import state_index, surface

class Isolated(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.base = Path(self.temp.name)
        self.patches = [patch.object(ins,'BASE',self.base),patch.object(ins,'STATE',self.base/'.state'),
                        patch.object(access,'STATE',self.base/'.state')]
        for p in self.patches: p.start()
        (self.base/'server.properties.example').write_text('online-mode=true\nmanagement-server-secret=\n')
        self.addCleanup(self.temp.cleanup)
        for p in self.patches: self.addCleanup(p.stop)
    def jar(self, version='1.0'):
        with zipfile.ZipFile(self.base/'server.jar','w') as archive:
            archive.writestr('version.json',json.dumps({'id':version,'java_version':21}))

class AuthTests(Isolated):
    def test_single_claim_and_hash(self):
        token=access.bootstrap_token()
        access.create_admin(token,'admin','long-test-password')
        self.assertTrue(access.authenticate('admin','long-test-password'))
        self.assertFalse(access.authenticate('admin','wrong-password'))
        self.assertNotIn('long-test-password',(ins.STATE/'admin.json').read_text())
        with self.assertRaises(ValueError): access.create_admin(token,'other','long-test-password')
        self.assertFalse((ins.STATE/'bootstrap.json').exists())
    def test_invalid_token_does_not_claim(self):
        access.bootstrap_token()
        with self.assertRaises(ValueError): access.create_admin('bad','admin','long-test-password')
        self.assertFalse((ins.STATE/'admin.json').exists())
    def test_parallel_claim(self):
        token=access.bootstrap_token(); outcomes=[]
        def claim():
            try: access.create_admin(token,'admin','long-test-password');outcomes.append(True)
            except ValueError: outcomes.append(False)
        threads=[threading.Thread(target=claim) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sum(outcomes),1)

class InstallTests(Isolated):
    def options(self):
        return {'version':'2.0','eula':True,'java':'java','memory_mb':2048,'online_mode':True}
    def meta(self):
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:archive.writestr('version.json',json.dumps({'id':'2.0','protocol_version':99}))
        payload=buffer.getvalue();return payload,{'id':'2.0','type':'release','releaseTime':'2026-02-01',
            'javaVersion':{'majorVersion':21},'downloads':{'server':{'url':'https://piston-data.mojang.com/server.jar',
             'size':len(payload),'sha1':hashlib.sha1(payload).hexdigest()}}}
    def response(self, payload):
        response=io.BytesIO(payload);response.url='https://piston-data.mojang.com/server.jar';return response
    def test_import_preserves_files_and_running_server(self):
        self.jar();(self.base/'world').mkdir();(self.base/'world/level.dat').write_bytes(b'world')
        (self.base/'server.properties').write_text('server-port=25566\nonline-mode=false\nmanagement-server-secret=private\n')
        before=(self.base/'server.properties').read_bytes()
        with patch.object(ins,'running',return_value=True): state=ins.import_existing()
        self.assertEqual(state['version'],'1.0'); self.assertTrue(state['complete'])
        self.assertEqual((self.base/'server.properties').read_bytes(),before)
        self.assertEqual((self.base/'world/level.dat').read_bytes(),b'world')
        self.assertTrue(list(ins.STATE.glob('import-backup-*')))
    def test_checksum_failure_retains_jar(self):
        self.jar();before=(self.base/'server.jar').read_bytes();payload,meta=self.meta()
        with patch.object(ins,'metadata',side_effect=lambda v:meta if v=='2.0' else {'releaseTime':'2026-01-01'}),patch.object(ins,'check_java'),patch.object(ins,'backup_world'),patch.object(ins.urllib.request,'urlopen',return_value=self.response(b'bad')):
            with self.assertRaisesRegex(ValueError,'checksum'):ins.install(self.options(),lambda *x:None)
        self.assertEqual((self.base/'server.jar').read_bytes(),before)
        self.assertFalse((self.base/'server.jar.part').exists());self.assertFalse(ins.settings()['complete'])
    def test_successful_install(self):
        payload,meta=self.meta()
        with patch.object(ins,'metadata',return_value=meta),patch.object(ins,'check_java'),patch.object(ins.urllib.request,'urlopen',return_value=self.response(payload)):
            result=ins.install(self.options(),lambda *x:None)
        self.assertEqual(result['version'],'2.0');self.assertEqual(result['memory_mb'],2048)
        self.assertEqual((self.base/'server.jar').read_bytes(),payload)
        self.assertEqual((self.base/'eula.txt').read_text(),'eula=true\n')
        self.assertEqual(ins.properties()['online-mode'],'true')
    def test_activation_failure_rolls_back_configuration(self):
        payload,meta=self.meta()
        (self.base/'server.properties').write_text('motd=original\n')
        original_replace=ins.os.replace
        def fail_jar(src,dest):
            if Path(dest)==self.base/'server.jar':raise OSError('activation failed')
            return original_replace(src,dest)
        with patch.object(ins,'metadata',return_value=meta),patch.object(ins,'check_java'),patch.object(ins.urllib.request,'urlopen',return_value=self.response(payload)),patch.object(ins.os,'replace',side_effect=fail_jar):
            with self.assertRaises(OSError):ins.install(self.options(),lambda *x:None)
        self.assertEqual((self.base/'server.properties').read_text(),'motd=original\n')
        self.assertFalse(ins.settings()['complete']);self.assertFalse((self.base/'eula.txt').exists())
    def test_backup_failure_blocks_download(self):
        self.jar();payload,meta=self.meta()
        with patch.object(ins,'metadata',side_effect=lambda v:meta if v=='2.0' else {'releaseTime':'2026-01-01'}),patch.object(ins,'check_java'),patch.object(ins,'backup_world',side_effect=OSError('disk full')),patch.object(ins.urllib.request,'urlopen') as download:
            with self.assertRaises(OSError):ins.install(self.options(),lambda *x:None)
            download.assert_not_called()
    def test_running_server_blocks_install(self):
        with patch.object(ins,'running',return_value=True):
            with self.assertRaisesRegex(ValueError,'Stop'):ins.install(self.options(),lambda *x:None)
    def test_downgrade_blocked(self):
        self.jar();payload,meta=self.meta()
        with patch.object(ins,'metadata',side_effect=lambda v:meta if v=='2.0' else {'releaseTime':'2026-03-01'}),patch.object(ins,'check_java'):
            with self.assertRaisesRegex(ValueError,'downgrade'):ins.install(self.options(),lambda *x:None)
    def test_snapshot_eula_and_offline_confirmations(self):
        data=self.options();data['eula']=False
        with self.assertRaisesRegex(ValueError,'EULA'):ins.install(data,lambda *x:None)
        data=self.options();data['online_mode']=False
        with self.assertRaisesRegex(ValueError,'offline'):ins.validate_options(data)
        payload,meta=self.meta();meta['type']='snapshot'
        with patch.object(ins,'metadata',return_value=meta):
            with self.assertRaisesRegex(ValueError,'snapshot'):ins.install(self.options(),lambda *x:None)
    def test_incompatible_and_missing_java(self):
        with patch.object(ins,'java_version',return_value=17):
            with self.assertRaisesRegex(ValueError,'21'):ins.check_java('java',21)
        with patch.object(ins.subprocess,'run',side_effect=FileNotFoundError):
            with self.assertRaisesRegex(ValueError,'not found'):ins.java_version('/missing/java')
    def test_offline_catalog(self):
        cache={'fetched_at':1,'latest':{},'versions':[]};ins.write_json(ins.STATE/'catalog.json',cache)
        with patch.object(ins,'fetch_json',side_effect=OSError):self.assertTrue(ins.catalog(True)['stale'])
        (ins.STATE/'catalog.json').unlink()
        with patch.object(ins,'fetch_json',side_effect=OSError):
            with self.assertRaisesRegex(ValueError,'Cannot reach'):ins.catalog()
    def test_filter_only_server_versions_and_snapshots(self):
        entries=[{'id':'release','type':'release'},{'id':'snap','type':'snapshot'},{'id':'client-only','type':'release'}]
        def meta(v):
            if v=='client-only':raise ValueError('no server')
            return {'javaVersion':{'majorVersion':21}}
        with patch.object(ins,'catalog',return_value={'versions':entries,'latest':{},'fetched_at':1,'stale':False}),patch.object(ins,'metadata',side_effect=meta):
            self.assertEqual([v['id'] for v in ins.available_versions()['versions']],['release'])
            self.assertEqual([v['id'] for v in ins.available_versions(True)['versions']],['release','snap'])
    def test_exclusive_operation(self):
        with ins.operation_lock():
            with self.assertRaises(ValueError):
                with ins.operation_lock():pass

class ArchiveTests(Isolated):
    def archive(self, names):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            for name in names:archive.writestr(name,b'world data')
        return data.getvalue()
    def test_traversal_does_not_replace_existing_world(self):
        from dashboard import world_jobs
        (self.base/'world').mkdir();(self.base/'world/level.dat').write_bytes(b'original')
        with patch.object(world_jobs,'BASE',self.base),patch.object(world_jobs,'running',return_value=False):
            with self.assertRaisesRegex(ValueError,'Unsafe'):
                world_jobs.import_world(self.archive(['world/level.dat','world/../../outside']))
        self.assertEqual((self.base/'world/level.dat').read_bytes(),b'original')
        self.assertFalse((self.base/'outside').exists())
    def test_import_keeps_prior_world_backup(self):
        from dashboard import world_jobs
        (self.base/'world').mkdir();(self.base/'world/level.dat').write_bytes(b'original')
        with patch.object(world_jobs,'BASE',self.base),patch.object(world_jobs,'running',return_value=False):
            world_jobs.import_world(self.archive(['world/level.dat']))
        self.assertEqual((self.base/'world/level.dat').read_bytes(),b'world data')
        self.assertEqual(next(self.base.glob('world_backup_prev_*/level.dat')).read_bytes(),b'original')

class TerrainTests(unittest.TestCase):
    def test_packed_signed_words_and_cross_boundary(self):
        self.assertEqual(state_index([-1]*400,12,5),31)
        self.assertEqual(state_index([-1]*400,12,5,False),31)
    def test_actual_top_block_not_first_palette_entry(self):
        data=[0]*256;data[0]=1 << (15*4)
        # Stone at local x=15,y=0; all other columns remain air.
        chunk={'DataVersion':3000,'sections':[{'Y':0,'block_states':{'palette':[{'Name':'minecraft:air'},{'Name':'minecraft:stone'}],'data':data}}]}
        result=surface(chunk,320)
        self.assertEqual(result[(15,0)],(0,'stone'));self.assertNotIn((0,0),result)
    def test_height_cutoff_and_negative_sections(self):
        chunk={'DataVersion':3000,'sections':[{'Y':-1,'block_states':{'palette':[{'Name':'minecraft:stone'}]}}]}
        self.assertEqual(surface(chunk,-5)[(0,0)],(-5,'stone'))

if __name__=='__main__':unittest.main()
