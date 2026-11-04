"""Sampled surface-height tiles for the browser's angled terrain renderer."""
import gzip
import json
import os
from pathlib import Path
import struct
import tempfile
import time
import zlib
from terrain import surface

DIMENSIONS = {'overworld':'overworld', 'nether':'the_nether', 'the_nether':'the_nether',
              'end':'the_end', 'the_end':'the_end'}
EMPTY = -32768

def dimension(value):
    try: return DIMENSIONS[value.removeprefix('minecraft:')]
    except (KeyError, AttributeError): raise ValueError('Unknown map dimension')

def color_for(block, palette):
    if block in palette: return palette[block]
    dyes = {'white':(226,226,220),'orange':(213,116,40),'magenta':(166,69,170),
            'light_blue':(85,157,199),'yellow':(224,190,56),'lime':(114,176,44),
            'pink':(214,133,159),'gray':(66,70,73),'light_gray':(150,151,145),
            'cyan':(35,119,138),'purple':(115,61,158),'blue':(55,67,153),
            'brown':(111,75,47),'green':(76,94,42),'red':(162,56,48),'black':(35,37,41)}
    for name in sorted(dyes, key=len, reverse=True):
        if block.startswith(name+'_') and any(s in block for s in ('wool','concrete','terracotta','glass','carpet')): return dyes[name]
    if 'leaves' in block: return (64,120,49)
    if 'glass' in block: return (157,187,190)
    if 'planks' in block or 'log' in block or 'wood' in block: return (143,109,69)
    if 'brick' in block: return (153,82,66)
    if 'copper' in block: return (158,100,68)
    if 'quartz' in block: return (218,214,201)
    if 'amethyst' in block: return (147,101,184)
    if 'torch' in block or 'lantern' in block: return (233,180,72)
    return (115,119,122)

def mesh_tile(region_dir, cache_dir, dim, rx, rz, step, layer, reader, palette):
    dim = dimension(dim)
    if step not in (2,4,8,16,32): raise ValueError('Invalid terrain detail')
    if not -64 <= layer <= 512: raise ValueError('Invalid height limit')
    if abs(rx) > 60000 or abs(rz) > 60000: raise ValueError('Region outside world bounds')
    if not region_dir: return None
    source = Path(region_dir) / f'r.{rx}.{rz}.mca'
    if not source.exists() or source.stat().st_size < 8192: return None
    stat = source.stat(); revision = str(stat.st_mtime_ns)
    cache_dir = Path(cache_dir); cache_dir.mkdir(parents=True, exist_ok=True)
    prefix = f'mesh-v1_{dim}_{rx}_{rz}_{step}_{layer}_'
    cache = cache_dir / f'{prefix}{revision}.json'
    if cache.exists():
        try: return json.loads(cache.read_text())
        except (OSError,ValueError): pass
    size = 512 // step
    heights = [EMPTY] * (size*size); colors = [0] * (size*size)
    loaded = skipped = 0
    started = time.monotonic()
    # Sample every chunk, but only decode the requested columns in each chunk.
    stride = min(step,16); sample_offset = stride//2
    with open(source,'rb') as stream:
        locations = struct.unpack('>1024I', stream.read(4096))
        for index, location in enumerate(locations):
            sector = location >> 8
            if not sector: continue
            cx,cz = index%32,index//32
            if step == 32 and (cx%2 or cz%2): continue
            try:
                stream.seek(sector*4096)
                length = struct.unpack('>I',stream.read(4))[0]
                compression = stream.read(1)[0]
                if compression & 0x80:
                    raw = (Path(region_dir)/f'c.{rx*32+cx}.{rz*32+cz}.mcc').read_bytes()
                    compression &= 0x7f
                else:
                    if length < 2 or length > 64*1024*1024: raise ValueError('Invalid chunk length')
                    raw = stream.read(length-1)
                if compression == 1: raw = gzip.decompress(raw)
                elif compression == 2: raw = zlib.decompress(raw)
                elif compression != 3: raise ValueError('Unsupported chunk compression')
                chunk = reader(raw).parse()
                if not isinstance(chunk,dict): raise ValueError('Unreadable chunk')
                columns = surface(chunk,layer,stride,sample_offset)
                loaded += 1
                for (x,z),(height,block) in columns.items():
                    gx,gz = (cx*16+x)//step,(cz*16+z)//step
                    target = gz*size+gx
                    heights[target] = height
                    r,g,b = color_for(block,palette)
                    colors[target] = (r<<16)|(g<<8)|b
            except (OSError,ValueError,IndexError,KeyError,struct.error,zlib.error,EOFError,TypeError):
                skipped += 1
    result = {'rx':rx,'rz':rz,'step':step,'size':size,'dimension':dim,'layer':layer,
              'revision':revision,'heights':heights,'colors':colors,'loaded_chunks':loaded,
              'skipped_chunks':skipped,'render_ms':round((time.monotonic()-started)*1000)}
    # Never cache a tile read while Minecraft was saving it, or one with unreadable chunks.
    if source.stat().st_mtime_ns == stat.st_mtime_ns and not skipped:
        fd,temp = tempfile.mkstemp(dir=cache_dir,suffix='.tmp')
        try:
            with os.fdopen(fd,'w') as dest: json.dump(result,dest,separators=(',',':'))
            os.replace(temp,cache)
        finally:
            if os.path.exists(temp): os.unlink(temp)
        for old in cache_dir.glob(prefix+'*.json'):
            if old != cache:
                try: old.unlink()
                except OSError: pass
    cached = list(cache_dir.glob('mesh-v1_*.json'))
    if len(cached) > 256:
        try:
            for old in sorted(cached,key=lambda p:p.stat().st_mtime)[:-256]: old.unlink(missing_ok=True)
        except OSError: pass
    return result

def read_spawn(world_dir, reader):
    fallback = {'x':0,'y':64,'z':0,'dimension':'overworld'}
    try:
        data = reader(gzip.decompress((Path(world_dir)/'level.dat').read_bytes())).parse()
        data = data.get('Data',data)
        if isinstance(data.get('spawn'),dict):
            value = data['spawn'];pos = value.get('pos',[0,64,0])
            return dict(x=int(pos[0]),y=int(pos[1]),z=int(pos[2]),dimension=dimension(value.get('dimension','overworld')))
        return dict(x=int(data.get('SpawnX',0)),y=int(data.get('SpawnY',64)),z=int(data.get('SpawnZ',0)),dimension='overworld')
    except (OSError,ValueError,TypeError,IndexError,AttributeError,EOFError,zlib.error): return fallback
