"""Owned update caches, separate from durable update/recovery state."""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
from runtime import app_data_dir,legacy_data_dir,PRODUCT_ID
from update_protocol import version
import diagnostics

APPLICATION=PRODUCT_ID
MARKER='.update-cache.json'
ENTRY='.cache-entry.json'
DAYS=7
LIMIT=1024**3
_gate=threading.RLock()
_local=threading.local()


def installation():return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent

def state_dir():return app_data_dir()/'updates'

def legacy_root(root):return root in (state_dir(),legacy_data_dir()/'updates')

def read_json(path):
    try:
        if path.stat().st_size>2*1024**2:return {}
        value=json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}

def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    try:temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)

def no_links(path):
    path=Path(os.path.abspath(path))
    for part in (path,*path.parents):
        try:
            info=part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&1024:raise ValueError('更新缓存路径不能经过链接或目录联接。')
        except FileNotFoundError:pass
    return path

def location():
    import preferences
    return no_links(preferences.load()['update_cache_dir'] or installation()/'update')

def belongs(data):
    try:return data.get('application')==APPLICATION and Path(data['install_dir']).resolve()==installation()
    except (KeyError,TypeError,ValueError):return False

def owner(root):return belongs(read_json(root/MARKER))

def validate_location(path):
    if not Path(path).is_absolute():raise ValueError('请选择绝对路径。')
    root=no_links(path);install=installation();data=app_data_dir().resolve()
    if not root.is_absolute() or root in (Path(root.anchor),Path.home(),data,install) or root in install.parents or root in data.parents:raise ValueError('不能使用磁盘根目录、程序根目录或用户数据目录作为缓存目录。')
    if root.is_relative_to(install) and root!=install/'update':raise ValueError('程序目录内只能使用专用的 update 文件夹。')
    if root==data/'updates' or root.is_relative_to(data/'updates') or root==legacy_data_dir()/'updates':raise ValueError('不能覆盖原更新状态目录。')
    return root

def register(root):
    root=validate_location(root)
    if root.exists() and not owner(root) and any(root.iterdir()):raise ValueError('所选目录非空且不属于当前程序，请选择空目录。')
    try:
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryFile(dir=root):pass
    except OSError:raise ValueError('更新缓存目录不可写，请在设置中选择其他目录。') from None
    write_json(root/MARKER,{'application':APPLICATION,'install_dir':str(installation())})
    path=state_dir()/'cache-locations.json';values=read_json(path).get('roots',[])
    values=[v for v in values if isinstance(v,str)] if isinstance(values,list) else []
    write_json(path,{'roots':list(dict.fromkeys([*values,str(root)]))[-32:]})
    return root

def roots():
    selected=location();result=[]
    values=read_json(state_dir()/'cache-locations.json').get('roots',[])
    for value in [str(selected),*(values if isinstance(values,list) else []),str(state_dir()),str(legacy_data_dir()/'updates')]:
        try:
            root=no_links(value)
            if legacy_root(root) or owner(root):
                if root not in result:result.append(root)
        except (OSError,ValueError,TypeError):pass
    return result

@contextmanager
def operation():
    if not _gate.acquire(blocking=False):raise RuntimeError('更新缓存正被使用，请稍后重试。')
    try:
        if getattr(_local,'depth',0):
            _local.depth+=1
            try:yield
            finally:_local.depth-=1
        else:
            from diagnostics import file_lock
            state_dir().mkdir(parents=True,exist_ok=True)
            with file_lock(state_dir()):
                _local.depth=1
                try:yield
                finally:_local.depth=0
    finally:_gate.release()

def new_stage(root):
    stage=Path(tempfile.mkdtemp(prefix='install-',dir=root));mark(stage,'stage');_local.stage=stage;return stage

def mark(path,kind,**extra):
    write_json(path/ENTRY,{'application':APPLICATION,'install_dir':str(installation()),'kind':kind,'used_at':time.time(),**extra})

def tree(path):
    files=[];dirs=[];pending=[path]
    while pending:
        folder=pending.pop();no_links(folder);dirs.append(folder)
        for item in folder.iterdir():
            no_links(item)
            if item.is_dir():pending.append(item)
            elif item.is_file():files.append(item)
            else:raise ValueError('缓存包含未知文件类型。')
    return files,dirs

def managed(path,legacy=False):
    meta=read_json(path/ENTRY)
    if belongs(meta):
        names={p.name for p in path.iterdir()}
        names={name for name in names if not re.fullmatch(r'(?:progress|window-ready|startup-ack|handoff-abort|result|plan)\.json\.(?:[0-9a-f]{32}\.)?tmp|plan\.tmp',name)}
        if meta.get('kind')=='package':return names<= {'package.zip','transfer.lock','writer.lock',ENTRY}
        if meta.get('kind')=='stage':
            if not names<= {'payload','base.json','target.json','target-download.json','plan.json','result.json','update_install.ps1','update_helper.ps1','update_window.ps1','progress.json','window-ready.json','startup-ack.json','handoff-abort.json','install.log','window-error.log',ENTRY}:return False
            inventory=read_json(path/'target.json')
            if inventory and (path/'payload').exists():
                from update_protocol import validate_inventory
                allowed={r['path'] for r in validate_inventory(inventory)['files']}
                files,_=tree(path/'payload')
                if any(p.relative_to(path/'payload').as_posix() not in allowed for p in files):return False
            return True
        return False
    if meta.get('application'):return False
    names={p.name for p in path.iterdir()}
    if re.fullmatch(r'package-[0-9a-f]{64}',path.name):return names<= {'package.zip','transfer.lock','writer.lock',ENTRY} and 'package.zip' in names
    if not legacy:return False
    plan=read_json(path/'plan.json')
    if not isinstance(plan.get('install_dir'),str) or not Path(plan['install_dir']).is_absolute():return False
    try:version(plan.get('version'))
    except ValueError:return False
    if path.name.startswith('release-'):
        return names<= {'package.zip','plan.json','result.json','update_helper.ps1'} and Path(plan.get('archive','')).resolve()==(path/'package.zip').resolve()
    if path.name.startswith(('install-','recovery-')):
        return names<= {'payload','base.json','target.json','target-download.json','plan.json','result.json','update_install.ps1','update_window.ps1','progress.json','window-ready.json','startup-ack.json','handoff-abort.json','install.log','window-error.log',ENTRY} and Path(plan.get('payload_dir','')).resolve()==(path/'payload').resolve()
    return False

def references():
    paths=set();invalid=False
    for name in ('prepared.json','pending.json','last-install.json','recovery-active.json'):
        file=state_dir()/name
        if not file.exists():continue
        data=read_json(file)
        try:
            value=Path(data['prepared']['plan']).parent if name=='prepared.json' else Path(data['stage'])
            if not value.is_absolute():raise ValueError('更新引用不是绝对路径。')
            paths.add(value.resolve())
            meta=read_json(value/ENTRY)
            if meta.get('archive'):paths.add(Path(meta['archive']).resolve().parent)
            plan=read_json(value/'plan.json')
            if plan.get('archive'):paths.add(Path(plan['archive']).resolve().parent)
        except (KeyError,TypeError,ValueError):invalid=True
    return paths,invalid

def entries():
    protected,invalid=references();result=[]
    for root in roots():
        if not root.is_dir():continue
        for path in root.iterdir():
            if not path.is_dir():
                if root!=state_dir() and path.name!=MARKER:
                    result.append({'path':path,'root':root,'size':path.stat().st_size,'used':time.time(),'known':False,'protected':True})
                continue
            try:
                files,_=tree(path);size=sum(p.stat().st_size for p in files)
                known=managed(path,legacy_root(root))
                used=read_json(path/ENTRY).get('used_at',max([path.stat().st_mtime,*[p.stat().st_mtime for p in files]]))
                if type(used) not in (int,float) or not math.isfinite(used):used=time.time()
                busy=invalid or path.resolve() in protected
                if legacy_root(root) and not read_json(path/ENTRY):
                    pid=read_json(path/'plan.json').get('pid',0)
                    if type(pid) is int and pid>0 and diagnostics.alive(pid):busy=True
                result.append({'path':path,'root':root,'size':size,'used':used,'known':known,'protected':busy})
            except (OSError,ValueError):result.append({'path':path,'root':root,'size':0,'used':time.time(),'known':False,'protected':True})
    return result

def remove_entry(row):
    root=no_links(row['root']);path=no_links(row['path'])
    if path.parent!=root or root not in roots() or not managed(path,legacy_root(root)):raise ValueError('拒绝删除未识别的缓存。')
    tree(path);shutil.rmtree(path)

def summary():
    rows=entries()
    return {'path':str(location()),'total':sum(r['size'] for r in rows),'reclaimable':sum(r['size'] for r in rows if r['known'] and not r['protected']),
            'protected':sum(r['size'] for r in rows if r['protected']),'unknown':sum(r['size'] for r in rows if not r['known']),
            'unknown_count':sum(not r['known'] for r in rows)}

def cleanup(manual=False,now=None,limit=LIMIT,days=DAYS):
    with operation():
        rows=entries();idle=sorted((r for r in rows if r['known'] and not r['protected']),key=lambda r:r['used']);remaining=sum(r['size'] for r in idle);freed=0;errors=[]
        for row in idle:
            if manual or (time.time() if now is None else now)-row['used']>days*86400 or remaining>limit:
                try:remove_entry(row);freed+=row['size'];remaining-=row['size']
                except (OSError,ValueError) as error:errors.append(str(error))
        return {**summary(),'freed':freed,'errors':errors}

def change_location(value):
    with operation():
        # Absolute plans remain valid until installed/cancelled; never move their payload.
        if any((state_dir()/name).exists() for name in ('prepared.json','pending.json')):raise ValueError('存在待安装更新，请先安装或处理该更新后再更换缓存位置。')
        import preferences
        root=register(validate_location(value or installation()/'update'))
        preferences.update(update_cache_dir=str(root) if value else '')
        return summary()

def reuse_package(cache,desc):
    destination=cache/'package.zip'
    if destination.exists():return
    for root in roots():
        old=root/cache.name
        if old==cache or not old.is_dir() or not managed(old,legacy_root(root)):continue
        source=old/'package.zip'
        if not source.is_file() or source.stat().st_size>desc['size']:continue
        temporary=cache/'package.copying'
        try:
            shutil.copy2(source,temporary);temporary.replace(destination)
            # The transfer layer validates the complete SHA-256, including reused partial bytes.
            mark(cache,'package');return
        finally:temporary.unlink(missing_ok=True)

def confirm_success(stage):
    """Called only after the running version matches a successful installer result."""
    with operation():
        stage=Path(stage);root=stage.parent
        if root not in roots() or not managed(stage,legacy_root(root)):return
        protected,invalid=references()
        payload=stage/'payload'
        if payload.exists():tree(payload);shutil.rmtree(payload)
        archive=read_json(stage/ENTRY).get('archive')
        # Keep tiny plan/result for result notices and independent repair diagnosis.
        # Shared archives are handled by reference-aware ordinary cleanup after acknowledgement.
        if archive:
            package=Path(archive).parent
            if package.parent==root and package.exists():
                refs=[]
                for name in ('prepared.json','pending.json'):
                    data=read_json(state_dir()/name)
                    if data:
                        try:refs.append(Path(data['prepared']['plan']).parent if name=='prepared.json' else Path(data['stage']))
                        except (KeyError,TypeError):return
                if not invalid and all(p.resolve()==stage.resolve() for p in refs):
                    remove_entry({'root':root,'path':package})
                    meta=read_json(stage/ENTRY);meta.pop('archive',None);write_json(stage/ENTRY,meta)

def abort_stage():
    stage=getattr(_local,'stage',None)
    if stage and stage.exists() and stage.resolve() not in references()[0]:remove_entry({'root':stage.parent,'path':stage})
    _local.stage=None


def maintain():
    with operation():
        from runtime import APP_VERSION
        data=read_json(state_dir()/'last-install.json')
        try:
            stage=Path(data['stage'])
            if stage.parent in roots():
                plan=read_json(stage/'plan.json');result=read_json(stage/'result.json')
                if plan.get('install_dir')==str(installation()) and plan.get('version')==APP_VERSION and result.get('ok') is True:confirm_success(stage)
        except (KeyError,TypeError,ValueError,OSError):pass
        return cleanup()



@contextmanager
def preparing():
    with operation():
        _local.stage=None
        try:yield
        except BaseException:
            try:abort_stage()
            except (OSError,ValueError):pass
            raise
        finally:_local.stage=None


def package_dir(root,sha):
    if not re.fullmatch('[0-9a-f]{64}',sha):raise ValueError('更新包标识无效。')
    path=no_links(root/('package-'+sha))
    if path.exists() and any(path.iterdir()) and not managed(path,legacy_root(root)):raise ValueError('更新包目录存在未知内容，已保留；请先选择其他缓存位置。')
    path.mkdir(exist_ok=True);mark(path,'package');return path
