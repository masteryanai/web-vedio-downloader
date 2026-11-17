"""Verified one-time legacy-data migration. Never falls back to a system-drive store."""
from contextlib import ExitStack,contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time
import uuid
from runtime import app_data_dir,legacy_data_dir,program_dir,APP_VERSION,PRODUCT_ID

MARKER='.portable-data.json'
JOURNAL='.legacy-migration.json'
UPDATE_STATE={'state.json','backup.json','pending.json','prepared.json','last-install.json','result-notice.json','release-catalog.json','cache-locations.json'}


def safe(path):
    path=Path(os.path.abspath(path))
    for part in (path,*path.parents):
        try:
            info=part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&1024:raise ValueError('数据迁移拒绝链接或目录联接。')
        except FileNotFoundError:pass
    return path

def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def read(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8-sig'));return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_name(path.name+'.tmp')
    try:temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)

def snapshot(source):
    result={};pending=[source]
    while pending:
        folder=pending.pop();safe(folder)
        for path in folder.iterdir():
            safe(path);name=path.relative_to(source).as_posix()
            if path.is_dir():
                if name=='updates':
                    for file in path.iterdir():
                        if file.name in UPDATE_STATE and file.is_file():
                            safe(file);info=file.stat();result[file.relative_to(source).as_posix()]=(info.st_size,info.st_mtime_ns)
                else:pending.append(path)
            elif path.is_file() and path.name not in ('instance.lock','writer.lock'):
                info=path.stat();result[name]=(info.st_size,info.st_mtime_ns)
    return result

@contextmanager
def quiet_legacy(source,cancel,progress):
    # Old installers start the new EXE before releasing this lock. Wait without
    # creating a lock or any new file in the old LocalAppData directory.
    with ExitStack() as stack:
        for path in (source/'writer.lock',source/'updates'/'writer.lock'):
            deadline=time.monotonic()+95
            while path.exists():
                if cancel():raise RuntimeError('已取消数据迁移，原数据未删除。')
                try:
                    handle=path.open('r+b')
                    if os.name=='nt':
                        import msvcrt
                        try:handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                        except OSError:handle.close();raise
                        stack.callback(lambda h=handle: h.close())
                    else:stack.callback(handle.close)
                    break
                except OSError:
                    if time.monotonic()>deadline:raise RuntimeError('旧更新器仍在运行，请等待更新完成后重试。')
                    progress('等待旧更新器完成…');time.sleep(.2)
        yield

def reconcile_updates(data):
    renamed={};folder=data/'updates'
    for name in ('prepared.json','pending.json','last-install.json'):
        path=folder/name
        if not path.exists():continue
        value=read(path)
        try:
            stage=Path(value['prepared']['plan']).parent if name=='prepared.json' else Path(value['stage'])
            plan=read(stage/'plan.json')
            valid=stage.is_absolute() and (stage/'plan.json').is_file() and Path(plan.get('install_dir','')).resolve()==program_dir()
            if name in ('prepared.json','pending.json'):valid=valid and plan.get('from_version')==APP_VERSION
            if valid and name=='prepared.json':valid=Path(value['prepared']['helper']).is_file() and (stage/'payload').is_dir()
        except (KeyError,TypeError,ValueError):valid=False
        if not valid:
            archived=folder/('obsolete-'+name)
            if archived.exists():archived=folder/('obsolete-'+uuid.uuid4().hex+'-'+name)
            path.replace(archived);renamed[path.relative_to(data).as_posix()]=archived.relative_to(data).as_posix()
    return renamed


def cleanup_originals(source,target,rows):
    leftovers=0
    for row in rows:
        path=safe(source/row['name']);copied=safe(target/row['destination'])
        if not path.exists():continue
        try:
            if digest(path)==row['sha256'] and copied.exists() and digest(copied)==row['sha256']:path.unlink()
            else:leftovers+=1
        except OSError:leftovers+=1
    # Remove only empty directories and stale lock files in the exact legacy root.
    # Unknown update payloads (if any) are deliberately not traversed or deleted.
    for folder in (source,source/'updates'):
        for name in ('writer.lock','instance.lock'):
            path=folder/name
            try:
                safe(path)
                if path.is_file():path.unlink()
            except OSError:pass
    directories=[];pending=[source]
    while pending:
        folder=pending.pop()
        if not folder.exists():continue
        safe(folder);directories.append(folder)
        for path in folder.iterdir():
            safe(path)
            if path.is_dir() and path!=source/'updates':pending.append(path)
    directories.append(source/'updates')
    for folder in sorted(set(directories),key=lambda p:len(p.parts),reverse=True):
        try:safe(folder);folder.rmdir()
        except OSError:pass
    return leftovers


def migrate(source,target,cancel=lambda:False,progress=lambda _:None):
    source=safe(source);target=safe(target)
    if source==target:return {'migrated':0,'conflicts':0,'leftovers':0}
    if source in target.parents or target in source.parents:raise ValueError('新旧数据目录不能互相包含。')
    target.mkdir(parents=True,exist_ok=True)
    if not source.exists():return {'migrated':0,'conflicts':0,'leftovers':0}
    with quiet_legacy(source,cancel,progress):
        files=snapshot(source);stage=Path(tempfile.mkdtemp(prefix='.migration-',dir=target));rows=[];conflicts=0
        try:
            for index,(name,attributes) in enumerate(files.items()):
                if cancel():raise RuntimeError('已取消数据迁移，原数据未删除。')
                path=safe(source/name);copy=stage/name;copy.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,copy)
                sha=digest(copy)
                if sha!=digest(path) or (path.stat().st_size,path.stat().st_mtime_ns)!=attributes:raise RuntimeError('旧数据发生变化，请先退出其他下载器后重试。')
                destination=name
                existing=safe(target/name)
                if existing.exists() and digest(existing)!=sha:
                    destination=('.migration-conflicts/'+name);conflicts+=1
                    if (target/destination).exists():destination='.migration-conflicts/'+uuid.uuid4().hex+'/'+name
                rows.append({'name':name,'destination':destination,'sha256':sha})
                progress(f'正在迁移并校验数据：{index+1}/{len(files)}')
            if snapshot(source)!=files:raise RuntimeError('旧数据发生变化，请先退出其他下载器后重试。')
            if cancel():raise RuntimeError('已取消数据迁移，原数据未删除。')
            # Commit only verified copies; partial commits can be retried without overwriting data.
            for row in rows:
                destination=safe(target/row['destination']);destination.parent.mkdir(parents=True,exist_ok=True)
                if not destination.exists():(stage/row['name']).replace(destination)
                if digest(destination)!=row['sha256']:raise RuntimeError('迁移文件校验失败，原数据保留。')
            write(target/JOURNAL,{'source':str(source),'files':rows})
        finally:
            if stage.parent!=target or not stage.name.startswith('.migration-'):raise ValueError('迁移暂存目录越界。')
            safe(stage);shutil.rmtree(stage)
    # Reconciliation changes only copied update-control records. Clean originals first
    # against the exact verified bytes, while all payloads remain outside this migration.
    leftovers=cleanup_originals(source,target,rows)
    renamed=reconcile_updates(target)
    if renamed:
        for row in rows:row['destination']=renamed.get(row['destination'],row['destination'])
        write(target/JOURNAL,{'source':str(source),'files':rows})
    return {'migrated':len(rows),'conflicts':conflicts,'leftovers':leftovers}


def relocate_data(data,old_root):
    old=Path(old_root);new=program_dir();old_cache=old/'update';new_cache=new/'update';old_data=old/'data'
    def remap(value):
        if isinstance(value,dict):return {k:remap(v) for k,v in value.items()}
        if isinstance(value,list):return [remap(v) for v in value]
        if isinstance(value,str):
            path=Path(value)
            if path.is_absolute():
                if path==old:return str(new)
                if path==old_cache or old_cache in path.parents:return str(new_cache/path.relative_to(old_cache))
                if path==old_data or old_data in path.parents:return str(data/path.relative_to(old_data))
        return value
    # Only update-control paths are rewritten; media output paths remain user-owned.
    for name in ('prepared.json','pending.json','last-install.json','cache-locations.json'):
        path=data/'updates'/name
        if path.exists():write(path,remap(read(path)))
    settings=data/'settings.json'
    if settings.exists():
        config=read(settings)
        if isinstance(config,dict) and config.get('update_cache_dir')==str(old_cache):config['update_cache_dir']=str(new_cache);write(settings,config)
    marker=new_cache/'.update-cache.json'
    if marker.exists():
        owner=read(marker)
        if owner.get('application')==PRODUCT_ID and Path(owner.get('install_dir',''))==old:
            safe(new_cache);write(marker,remap(owner))
            for entry in new_cache.iterdir():
                if not entry.is_dir():continue
                safe(entry);metadata=read(entry/'.cache-entry.json')
                if metadata.get('application')!=PRODUCT_ID or Path(metadata.get('install_dir',''))!=old:continue
                write(entry/'.cache-entry.json',remap(metadata))
                if (entry/'plan.json').exists():write(entry/'plan.json',remap(read(entry/'plan.json')))
    reconcile_updates(data)


def initialize(cancel=lambda:False,progress=lambda _:None):
    target=safe(app_data_dir())
    try:
        target.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryFile(dir=target):pass
    except OSError:raise RuntimeError('程序目录中的 data 无法写入，请将完整程序放到可写目录后重试；不会改用 C 盘。') from None
    marker=read(target/MARKER)
    names={p.name for p in target.iterdir()}-{'writer.lock','instance.lock'}
    if (target/MARKER).exists() and marker.get('application')!=PRODUCT_ID:raise ValueError('data 目录标记无效，原文件已保留；请使用独立程序目录。')
    if not (target/MARKER).exists() and names and not os.environ.get('WVD_DATA_DIR'):raise ValueError('data 目录非空且无法确认归属，原文件已保留；请使用独立程序目录。')
    from diagnostics import file_lock
    with file_lock(target):
        marker=read(target/MARKER)
        if not marker:
            marker={'application':PRODUCT_ID,'legacy_done':False,'install_dir':str(program_dir())};write(target/MARKER,marker)
        if marker.get('application')==PRODUCT_ID and marker.get('legacy_done') is True:
            if not isinstance(marker.get('install_dir'),str) or not Path(marker['install_dir']).is_absolute():raise ValueError('便携数据标记损坏，原数据已保留，请检查 data 目录。')
            if marker.get('install_dir')!=str(program_dir()):
                relocate_data(target,marker['install_dir']);marker['install_dir']=str(program_dir());write(target/MARKER,marker)
            return {**marker,'already_initialized':True}
        result={'migrated':0,'conflicts':0,'leftovers':0}
        if getattr(sys,'frozen',False) and not os.environ.get('WVD_DATA_DIR'):
            if legacy_data_dir().exists() and not os.environ.get('WVD_ALLOW_TEST_PEERS'):
                from instance_identity import other_downloaders
                deadline=time.monotonic()+8
                while other_downloaders():
                    if cancel():raise RuntimeError('已取消数据迁移。')
                    if time.monotonic()>deadline:raise RuntimeError('请先退出其他下载器进程（包括命令行任务），再启动数据迁移。')
                    progress('等待旧版下载器退出…');time.sleep(.2)
            result=migrate(legacy_data_dir(),target,cancel,progress)
        write(target/MARKER,{'application':PRODUCT_ID,'legacy_done':True,'install_dir':str(program_dir()),**result})
        return result
