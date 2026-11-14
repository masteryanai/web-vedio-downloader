"""Bounded, redacted diagnostics; no network submission or credential collection."""
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import platform
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from urllib.parse import urlsplit
from runtime import APP_VERSION,app_data_dir

LOG_SIZE=5*1024*1024
LOG_FILES=5
HANG_SECONDS=15
_current=None
_stages={}


def redact(value):
    text=str(value)
    # Header values can contain multiple cookies or a Bearer prefix: remove the whole suffix.
    text=re.sub(r'(?im)\b(cookie|authorization|proxy-authorization)\s*[\"\x27]?\s*[:=][^\r\n]*',r'\1=[REDACTED]',text)
    text=re.sub(r'(?i)([\"\x27]?\b(?:sessdata|session(?:id)?|access[_-]?token|refresh[_-]?token|auth_key|api[_-]?key|csrf_token|bili_jct|token|password|passwd|secret)\b[\"\x27]?\s*[:=]\s*)(?:\"[^\"]*\"|\x27[^\x27]*\x27|[^\s,;}]+)',r'\1[REDACTED]',text)
    text=re.sub(r'(?im)\b(sessdata|session(?:id)?|access[_-]?token|refresh[_-]?token|auth_key|api[_-]?key|csrf_token|bili_jct|token|password|passwd|secret)\s*[\"\x27]?\s*[:=][^\r\n]*',r'\1=[REDACTED]',text)
    def url(match):
        try:return '[URL:'+str(urlsplit(match[0]).hostname or 'hidden')+']'
        except ValueError:return '[URL]'
    text=re.sub(r'https?://[^\s<>\"\x27]+',url,text,flags=re.I)
    text=re.sub(r'\\\\[^\\\r\n]+\\[^\r\n]*','[LOCAL_PATH]',text)
    text=re.sub(r'(?i)\b[A-Z]:[\\/][^\r\n]*','[LOCAL_PATH]',text)
    text=re.sub(r'(?<!\w)/(?:home|Users|tmp|var|mnt|private)/[^\s\"\x27]*','[LOCAL_PATH]',text)
    for name in {os.environ.get('USERNAME',''),os.environ.get('USER',''),Path.home().name}:
        if name and len(name)>1:text=re.sub(re.escape(name),'[USER]',text,flags=re.I)
    return text[:12000]


def root():return app_data_dir()/'diagnostics'


def atomic_json(path,data):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


@contextmanager
def file_lock(folder):
    with (folder/'writer.lock').open('a+b') as guard:
        if not guard.tell():guard.write(b'0');guard.flush()
        deadline=time.monotonic()+1
        while True:
            try:
                if os.name=='nt':
                    import msvcrt
                    guard.seek(0);msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()>deadline:raise
                time.sleep(.02)
        try:yield
        finally:
            if os.name=='nt':guard.seek(0);msvcrt.locking(guard.fileno(),msvcrt.LK_UNLCK,1)


def append_log(folder,line):
    try:clean=json.dumps(scrub(json.loads(line)),ensure_ascii=False)
    except (ValueError,TypeError):clean=redact(line)
    content=(clean+'\n').encode('utf-8')
    with file_lock(folder):
        path=folder/'runtime.log'
        if path.exists() and path.stat().st_size+len(content)>LOG_SIZE:
            for index in range(LOG_FILES-1,0,-1):
                target=folder/f'runtime.log.{index}'
                source=folder/('runtime.log' if index==1 else f'runtime.log.{index-1}')
                if source.exists():source.replace(target)
        with path.open('ab') as output:output.write(content)


def stacks():
    result=[]
    names={thread.ident:thread.name for thread in threading.enumerate()}
    for ident,frame in list(sys._current_frames().items())[:16]:
        frames=[]
        while frame and len(frames)<32:
            filename=frame.f_code.co_filename.replace('\\','/').rsplit('/',1)[-1]
            frames.append(f'{filename}:{frame.f_lineno} {frame.f_code.co_name}');frame=frame.f_back
        result.append({'thread':ident,'name':redact(names.get(ident,'unknown')),'frames':frames})
    return result


def alive(pid):
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
        kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return ctypes.get_last_error()==5
        try:
            code=ctypes.c_uint32()
            return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
        finally:kernel.CloseHandle(handle)
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True


def scrub(value):
    if isinstance(value,dict):
        return {redact(str(key)):('[REDACTED]' if re.fullmatch(r'(?i)(?:cookies?|authorization|proxy-authorization|sessdata|session(?:id)?|access[_-]?token|refresh[_-]?token|auth_key|api[_-]?key|csrf_token|bili_jct|token|password|passwd|secret)',str(key)) else scrub(item)) for key,item in value.items()}
    if isinstance(value,list):return [scrub(item) for item in value]
    return redact(value) if isinstance(value,str) else value


def process_token(pid):
    if os.name!='nt':return None
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
    kernel.GetProcessTimes.argtypes=[ctypes.c_void_p]+[ctypes.POINTER(ctypes.c_uint64)]*4
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return None
    try:
        values=[ctypes.c_uint64() for _ in range(4)]
        if kernel.GetProcessTimes(handle,*[ctypes.byref(value) for value in values]):return values[0].value
    finally:kernel.CloseHandle(handle)
    return None


def save_incident(folder,data,path=None):
    folder.mkdir(parents=True,exist_ok=True)
    path=path or folder/('incident-'+uuid.uuid4().hex+'.json')
    # Input is already structured without locals/source text; redact again before disk.
    data=scrub(data)
    with file_lock(folder):
        atomic_json(path,data)
        for old in sorted(folder.glob('incident-*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[5:]:old.unlink(missing_ok=True)
    return path


def watchdog(session,threshold=HANG_SECONDS,poll=.5):
    session=Path(session).resolve()
    if session.parent!=(root()/'sessions').resolve():raise ValueError('无效诊断会话目录')
    meta=json.loads((session/'meta.json').read_text(encoding='utf-8'));pid=meta['pid']
    if type(pid) is not int or pid<=0:raise ValueError('无效诊断进程')
    last_sequence=None;changed=time.monotonic();incident=None;data={};last_snapshot={}
    while True:
        if (session/'clean.json').exists():return
        running=alive(pid)
        if running and meta.get('process_token'):
            token=process_token(pid)
            if token is not None and token!=meta['process_token']:running=False
        try:last_snapshot=json.loads((session/'heartbeat.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        sequence=last_snapshot.get('sequence')
        if sequence!=last_sequence:
            if incident:
                data['recovered_at']=time.time();save_incident(root(),data,incident);incident=None
            last_sequence=sequence;changed=time.monotonic()
        if not running:
            if not (session/'clean.json').exists():
                save_incident(root(),{'kind':'abnormal_exit','detected_at':time.time(),'run_id':meta['run_id'],'version':meta['version'],'last_snapshot':last_snapshot})
            return
        if time.monotonic()-changed>=threshold and incident is None:
            data={'kind':'ui_unresponsive','detected_at':time.time(),'run_id':meta['run_id'],'version':meta['version'],
                  'snapshot_note':'背景采样的 Python 堆栈；若全局解释器阻塞，可能为卡死前最后采样，并非完整内存转储。','last_snapshot':last_snapshot}
            incident=save_incident(root(),data)
        time.sleep(poll)


def clean_sessions(folder):
    base=(folder/'sessions').resolve()
    candidates=sorted(base.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True)
    for old in candidates[10:]:
        try:
            if old.is_symlink() or old.resolve().parent!=base:continue
            meta=json.loads((old/'meta.json').read_text(encoding='utf-8'))
            if not (old/'clean.json').exists() and alive(meta['pid']):continue
            for name in ('meta.json','heartbeat.json','clean.json'):(old/name).unlink(missing_ok=True)
            old.rmdir()
        except (OSError,ValueError,KeyError):pass


class Recorder:
    def __init__(self,folder=None,spawn=True):
        self.folder=Path(folder) if folder else root();self.folder.mkdir(parents=True,exist_ok=True)
        self.session=uuid.uuid4().hex;self.session_dir=self.folder/'sessions'/self.session
        self.session_dir.mkdir(parents=True);self.sequence=0;self.phase='启动';self.closed=False;self.dropped=0
        self.pending=queue.Queue(maxsize=2048);self.stop_event=threading.Event();self.child=None
        atomic_json(self.session_dir/'meta.json',{'run_id':self.session,'pid':os.getpid(),'process_token':process_token(os.getpid()),'version':APP_VERSION,'started_at':time.time()})
        self.writer=threading.Thread(target=self._write,name='diagnostic-writer',daemon=True);self.writer.start()
        self.sampler=threading.Thread(target=self._sample,name='diagnostic-sampler',daemon=True);self.sampler.start()
        if spawn:
            command=[sys.executable,'--diagnostic-watchdog',str(self.session_dir)] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).with_name('main.py')),'--diagnostic-watchdog',str(self.session_dir)]
            try:self.child=subprocess.Popen(command,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            except OSError as error:self.event('watchdog.unavailable',type(error).__name__)
        self.event('session.start',f'version={APP_VERSION} system={platform.system()} {platform.release()} python={platform.python_version()}')

    def event(self,kind,message=''):
        safe=redact(message);self.phase=redact(kind+': '+safe)
        line=json.dumps({'time':time.time(),'run_id':self.session,'thread':threading.current_thread().name,'thread_id':threading.get_ident(),'event':redact(kind),'message':safe},ensure_ascii=False)
        try:self.pending.put_nowait(line)
        except queue.Full:self.dropped+=1

    def beat(self):self.sequence+=1

    def _write(self):
        while True:
            item=self.pending.get()
            try:
                if item is None:return
                append_log(self.folder,item)
            except (OSError,ValueError):self.dropped+=1
            finally:self.pending.task_done()

    def _sample(self):
        try:clean_sessions(self.folder)
        except OSError:pass
        while not self.stop_event.wait(.5):
            if self.child and self.child.poll() is not None:
                self.event('watchdog.stopped','现场监测进程意外退出');self.child=None
            try:atomic_json(self.session_dir/'heartbeat.json',{'sequence':self.sequence,'sampled_at':time.time(),'phase':self.phase,'dropped_logs':self.dropped,'threads':stacks()})
            except (OSError,ValueError):pass

    def stop(self):
        if self.closed:return
        self.closed=True;self.event('session.stop');self.stop_event.set()
        self.sampler.join(timeout=1)
        try:atomic_json(self.session_dir/'clean.json',{'clean':True,'time':time.time()})
        except OSError:pass
        try:self.pending.put(None,timeout=.2);self.writer.join(timeout=1)
        except queue.Full:pass
        if self.child:
            try:self.child.wait(timeout=2)
            except subprocess.TimeoutExpired:self.dropped+=1


def event(kind,message=''):
    if _current and not _current.closed:
        try:_current.event(kind,message)
        except Exception:pass


def stage(name):
    if not _current or _current.closed:return
    ident=threading.get_ident();now=time.monotonic();previous=_stages.get(ident)
    if previous and (previous[0]==name or now-previous[1]<.2):return
    if len(_stages)>128:_stages.clear()
    _stages[ident]=(name,now)
    event('stage.changed',f'{name}; previous_duration={now-previous[1]:.3f}s' if previous else name)


def exception(kind,error,tb=None):
    frames=[]
    while tb:
        frames.append(f'{tb.tb_frame.f_code.co_filename.replace('\\','/').rsplit('/',1)[-1]}:{tb.tb_lineno} {tb.tb_frame.f_code.co_name}');tb=tb.tb_next
    event(kind,f'{type(error).__name__}: {error}\n'+'\n'.join(frames))


def start():
    global _current
    if _current and not _current.closed:return _current
    try:_current=Recorder()
    except (OSError,ValueError):return None
    sys.excepthook=lambda typ,error,tb:exception('exception.main',error,tb)
    threading.excepthook=lambda args:exception('exception.thread',args.exc_value,args.exc_traceback)
    sys.unraisablehook=lambda args:exception('exception.unraisable',args.exc_value,args.exc_traceback)
    return _current


def health():
    return {'started':bool(_current and not _current.closed),
            'independent_watchdog':bool(_current and _current.child and _current.child.poll() is None),
            'dropped_records':_current.dropped if _current else 0}


def unreported():
    try:
        notified=set(json.loads((root()/'notified.json').read_text())) if (root()/'notified.json').exists() else set()
        return [p.name for p in root().glob('incident-*.json') if p.name not in notified]
    except (OSError,ValueError):return []


def acknowledge(names):
    try:atomic_json(root()/'notified.json',list({p.name for p in root().glob('incident-*.json')}|set(names)))
    except OSError:pass


def export_bundle(destination,description=''):
    """Whitelist bounded diagnostics only; never recurse through app data or task payloads."""
    folder=root();report_id=uuid.uuid4().hex
    metadata={'report_id':report_id,'version':APP_VERSION,'system':platform.system(),'release':platform.release(),
              'python':platform.python_version(),'logging_health':health(),'description':redact(description),'created_at':time.time()}
    try:
        import download_tasks as tasks
        counts={}
        for task in tasks.records():
            key=task['state'];counts[key]=counts.get(key,0)+1
        metadata['task_counts']=counts
    except Exception:metadata['task_counts']='不可读取'
    destination=Path(destination);temporary=destination.with_name(destination.name+'.'+report_id+'.tmp')
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr('summary.json',json.dumps(metadata,ensure_ascii=False,indent=2))
            bundle.writestr('threads.json',json.dumps(scrub({'sampled_at':time.time(),'phase':_current.phase if _current else '无活动诊断会话','threads':stacks()}),ensure_ascii=False))
            candidates=[folder/'runtime.log']+[folder/f'runtime.log.{i}' for i in range(1,LOG_FILES)]+sorted(folder.glob('incident-*.json'))[-5:]
            for path in candidates:
                if not path.is_file() or path.is_symlink():continue
                limit=LOG_SIZE if path.name.startswith('runtime.log') else 256*1024
                with file_lock(folder):
                    if not path.exists():continue
                    with path.open('rb') as stream:
                        if path.stat().st_size>limit:stream.seek(-limit,2)
                        text=stream.read(limit).decode('utf-8',errors='replace')
                # Re-redact old records as well; filenames are the fixed whitelist above.
                bundle.writestr(path.name,json.dumps(scrub(json.loads(text)),ensure_ascii=False) if path.suffix=='.json' else redact_export(text))
        temporary.replace(destination)
    finally:temporary.unlink(missing_ok=True)
    return report_id


def redact_export(text):
    lines=[]
    for line in text.splitlines():
        try:lines.append(json.dumps(scrub(json.loads(line)),ensure_ascii=False))
        except ValueError:lines.append(redact(line))
    return '\n'.join(lines)
