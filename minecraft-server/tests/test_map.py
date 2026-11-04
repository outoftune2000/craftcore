import gzip
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from map_terrain import dimension,mesh_tile,read_spawn,EMPTY,color_for
from terrain import surface

class Reader:
    def __init__(self,data):pass
    def parse(self):return {'DataVersion':4788,'sections':[{'Y':4,'block_states':{'palette':[{'Name':'minecraft:stone'}]}}]}

class MapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.region=self.base/'region';self.region.mkdir();self.cache=self.base/'cache'
    def write_region(self,compression=2):
        locations=[0]*1024;locations[0]=(2<<8)|1
        payload=zlib.compress(b'fixture')
        chunk=struct.pack('>I',len(payload)+1)+bytes([compression])+payload
        path=self.region/'r.-1.2.mca'
        path.write_bytes(struct.pack('>1024I',*locations)+bytes(4096)+chunk+bytes(4096-len(chunk)))
        return path
    def tile(self,**kwargs):
        return mesh_tile(self.region,self.cache,kwargs.get('dim','overworld'),-1,2,kwargs.get('step',8),kwargs.get('layer',320),kwargs.get('reader',Reader),{'stone':(100,110,120)})
    def test_aliases_and_invalid_dimension(self):
        for value in ['nether','the_nether','minecraft:the_nether']:self.assertEqual(dimension(value),'the_nether')
        self.assertEqual(dimension('end'),'the_end')
        with self.assertRaises(ValueError):dimension('../world')
    def test_sample_height_color_and_negative_region(self):
        self.write_region();tile=self.tile()
        self.assertEqual(tile['size'],64);self.assertEqual(tile['rx'],-1);self.assertEqual(tile['rz'],2)
        self.assertEqual(tile['heights'][0],79);self.assertEqual(tile['colors'][0],0x646e78)
        self.assertEqual(tile['heights'][10],EMPTY);self.assertEqual(tile['loaded_chunks'],1)
    def test_cutoff_samples_at_requested_height(self):
        self.write_region();self.assertEqual(self.tile(layer=70)['heights'][0],70)
        self.assertEqual(self.tile(layer=50)['heights'][0],EMPTY)
    def test_cache_hit_and_region_revision(self):
        path=self.write_region();first=self.tile()
        with patch.object(Reader,'parse',side_effect=AssertionError('cache missed')):self.assertEqual(self.tile(),first)
        import os
        stat=path.stat();os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
        self.assertNotEqual(self.tile()['revision'],first['revision'])
        self.assertEqual(len(list(self.cache.glob('*.json'))),1)
    def test_invalid_parameters_and_missing_region(self):
        self.assertIsNone(self.tile())
        with self.assertRaises(ValueError):self.tile(step=7)
        with self.assertRaises(ValueError):self.tile(layer=1000)
    def test_unsupported_chunks_reported_not_cached(self):
        self.write_region(compression=4);tile=self.tile()
        self.assertEqual(tile['skipped_chunks'],1);self.assertFalse(list(self.cache.glob('*.json')))
    def test_modern_spawn_and_legacy_spawn(self):
        (self.base/'level.dat').write_bytes(gzip.compress(b'fixture'))
        class SpawnReader(Reader):
            def parse(self):return {'Data':{'spawn':{'pos':[128,70,-256],'dimension':'minecraft:overworld'}}}
        self.assertEqual(read_spawn(self.base,SpawnReader),{'x':128,'y':70,'z':-256,'dimension':'overworld'})
        class LegacyReader(Reader):
            def parse(self):return {'Data':{'SpawnX':12,'SpawnY':65,'SpawnZ':-4}}
        self.assertEqual(read_spawn(self.base,LegacyReader)['z'],-4)
    def test_all_air_and_sampled_columns(self):
        chunk=Reader(b'').parse();self.assertEqual(len(surface(chunk,320,8,4)),4)
        chunk['sections'][0]['block_states']['palette'][0]['Name']='minecraft:air'
        self.assertEqual(surface(chunk,320,8,4),{})
    def test_bad_spawn_file_falls_back(self):
        (self.base/'level.dat').write_bytes(b'invalid gzip')
        self.assertEqual(read_spawn(self.base,Reader),{'x':0,'y':64,'z':0,'dimension':'overworld'})
    def test_block_colors_for_buildings(self):
        self.assertEqual(color_for('red_concrete',{}),(162,56,48))
        self.assertNotEqual(color_for('oak_log',{}),color_for('glass',{}))

if __name__=='__main__':unittest.main()
