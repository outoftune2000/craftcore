"""Blocking world archive work; call through asyncio.to_thread."""
import io
import os
from pathlib import Path
import shutil
import tempfile
import time
import zipfile
from installation import BASE, operation_lock, running

def export_world():
    with operation_lock():
        if running(): raise ValueError('Stop Minecraft before exporting a consistent world archive')
        fd, name = tempfile.mkstemp(suffix='.zip'); os.close(fd)
        try:
            with zipfile.ZipFile(name, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in (BASE / 'world').rglob('*'):
                    if path.is_file() and path.name != 'session.lock': archive.write(path, path.relative_to(BASE))
            return name
        except Exception:
            os.unlink(name); raise

def import_world(payload):
    with operation_lock():
        if running(): raise ValueError('Stop Minecraft before importing a world')
        world = BASE / 'world'
        with tempfile.TemporaryDirectory(dir=BASE, prefix='.world-import-') as temp:
            staging = Path(temp)
            with zipfile.ZipFile(io.BytesIO(payload) if isinstance(payload, bytes) else payload) as archive:
                files = [v for v in archive.infolist() if not v.is_dir() and not v.filename.startswith('__MACOSX')]
                levels = [v for v in files if Path(v.filename).name == 'level.dat']
                if len(levels) != 1: raise ValueError('Archive must contain exactly one world level.dat')
                prefix = Path(levels[0].filename).parent
                if sum(v.file_size for v in files) > 20 * 1024**3: raise ValueError('Expanded archive exceeds 20 GB')
                for member in files:
                    name = member.filename.replace('\\', '/')
                    if name.startswith('/') or ':' in name or '..' in Path(name).parts: raise ValueError('Unsafe archive path')
                    if (member.external_attr >> 16) & 0o170000 == 0o120000: raise ValueError('Archive symlinks are forbidden')
                    try: relative = Path(name).relative_to(prefix)
                    except ValueError: continue
                    target = (staging / relative).resolve()
                    if not target.is_relative_to(staging.resolve()): raise ValueError('Unsafe archive path')
                    if target.name == 'session.lock': continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, open(target, 'wb') as dest: shutil.copyfileobj(source, dest)
            if not (staging / 'level.dat').exists(): raise ValueError('Missing world metadata')
            old = BASE / ('world_backup_prev_' + str(time.time_ns()))
            if world.exists(): os.replace(world, old)
            try: shutil.copytree(staging, world)
            except Exception:
                if world.exists(): shutil.rmtree(world)
                if old.exists(): os.replace(old, world)
                raise
