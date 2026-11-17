"""Self describing, validated library records. No network or media deletion."""
import copy,hashlib,json,os,uuid,stat,threading,time,re
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode

FORMAT=1
DIRECTORY='.wvd-library'
_io_lock=threading.RLock()

def synchronized(function):
 @wraps(function)
 def guarded(*args,**kwargs):
  with _io_lock:return function(*args,**kwargs)
 return guarded

def sharing_retry(operation):
 delays=(.01,.02,.04,.08)
 for attempt in range(len(delays)+1):
  try:return operation()
  except PermissionError as error:
   if os.name!='nt' or getattr(error,'winerror',None) not in (5,32,33) or attempt==len(delays):raise
   time.sleep(delays[attempt])

def replace(source,target):return sharing_retry(lambda:os.replace(source,target))

def reject_links(path):
 for part in (Path(path),*Path(path).parents):
  try:info=part.lstat()
  except FileNotFoundError:continue
  if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&1024:raise ValueError('路径不能经过符号链接或目录联接。')

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')

def web_url(value):
 try:
  p=urlsplit(value)
  if p.scheme not in ('http','https') or not p.hostname:return ''
  if p.path.lower().endswith(('.m3u8','.mp4','.ts','.mpd')):return ''
  stable={'id','vid','bvid','aid','cid','season_id','episode_id','ep_id','p','v','tvid','albumid'}
  query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() in stable and not re.search(r'https?://',v,re.I)]
  return urlunsplit((p.scheme,p.hostname+(':'+str(p.port) if p.port else ''),p.path,urlencode(query),''))
 except ValueError:return ''

def sanitize(value):
 if isinstance(value,dict):
  result={}
  for k,v in value.items():
   if '://' not in k and any(s in k.lower() for s in ('cookie','token','password','headers','authorization')):continue
   key=sanitize(k)
   if key in result:raise ValueError('规范化来源身份发生冲突，请核对来源。')
   result[key]=web_url(v) if k.endswith('url') and isinstance(v,str) else sanitize(v)
  return result
 if isinstance(value,list):return [sanitize(v) for v in value]
 if isinstance(value,str) and re.search(r'https?://',value):return re.sub(r'https?://[^\s|]+',lambda match:web_url(match.group()),value)
 return value

def encode(payload):return {'format':FORMAT,'payload':payload,'sha256':hashlib.sha256(canonical(payload)).hexdigest()}

@synchronized
def load(path):
 reject_links(path)
 record=json.loads(Path(path).read_text(encoding='utf-8'))
 if not isinstance(record,dict):raise ValueError('存储库资料结构无效。')
 if record.get('format')!=FORMAT:raise ValueError('无法识别的存储库格式；原件保留。')
 payload=record.get('payload')
 if not isinstance(payload,dict):raise ValueError('存储库资料结构无效。')
 if hashlib.sha256(canonical(payload)).hexdigest()!=record.get('sha256'):raise ValueError('存储库资料校验失败；原件保留。')
 if not isinstance(payload.get('library'),dict) or not isinstance(payload.get('works'),list) or not isinstance(payload.get('episodes'),list):raise ValueError('存储库资料结构无效。')
 libid=payload['library'].get('id')
 if not isinstance(libid,str) or not libid:raise ValueError('存储库身份无效。')
 works=payload['works'];episodes=payload['episodes']
 if any(not isinstance(w,dict) or not isinstance(w.get('id'),str) or w.get('library_id')!=libid for w in works):raise ValueError('作品身份无效。')
 ids={w['id'] for w in works}
 if len(ids)!=len(works):raise ValueError('作品身份重复。')
 if any(not isinstance(e,dict) or not isinstance(e.get('id'),str) or e.get('work_id') not in ids or not isinstance(e.get('files',[]),list) for e in episodes):raise ValueError('选集身份无效。')
 if len({e['id'] for e in episodes})!=len(episodes):raise ValueError('选集身份重复。')
 for e in episodes:
  if any(not isinstance(f,dict) or not isinstance(f.get('path'),str) or f.get('location') not in ('library','external') for f in e.get('files',[])):raise ValueError('文件关联无效。')
 return payload

@synchronized
def atomic(path,value):
 path=Path(path);reject_links(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name('.'+uuid.uuid4().hex+'.tmp')
 try:
  with temp.open('wb') as out:out.write(canonical(value));out.flush();os.fsync(out.fileno())
  replace(temp,path)
 finally:temp.unlink(missing_ok=True)

_held_names=set()

class Lease:
 def __init__(self,root):
  self.file=None;self.handle=None;self.name=None
  if os.name=='nt':
   import threading
   name='Global\\WVDLibrary-'+hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode()).hexdigest()
   if name in _held_names:raise OSError('另一实例正在写入存储库。')
   ready=threading.Event();stop=threading.Event();state={}
   def guardian():
    import ctypes
    create=ctypes.windll.kernel32.CreateMutexW;create.restype=ctypes.c_void_p
    handle=create(None,False,name)
    wait=ctypes.windll.kernel32.WaitForSingleObject;wait.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
    close=ctypes.windll.kernel32.CloseHandle;close.argtypes=[ctypes.c_void_p]
    if not handle:state['error']=ctypes.WinError();ready.set();return
    if wait(handle,0) not in (0,0x80):
     close(handle);state['error']=OSError('另一程序正在写入存储库。');ready.set();return
    ready.set();stop.wait()
    release=ctypes.windll.kernel32.ReleaseMutex;release.argtypes=[ctypes.c_void_p];release(handle);close(handle)
   thread=threading.Thread(target=guardian,daemon=True);thread.start();ready.wait()
   if 'error' in state:raise state['error']
   self.stop=stop;self.thread=thread;self.name=name;_held_names.add(name)
  else:
   import fcntl
   folder=Path(root)/DIRECTORY;folder.mkdir(exist_ok=True);self.file=(folder/'writer.lock').open('a+b')
   try:fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BaseException:self.file.close();raise
 def close(self):
  if self.name:_held_names.discard(self.name);self.name=None
  if getattr(self,'stop',None):self.stop.set();self.thread.join();self.stop=None
  if self.file and not self.file.closed:self.file.close()
 def __del__(self):
  try:self.close()
  except Exception:pass

def resolve(root,value):
 p=Path(value)
 if p.is_absolute() or p.drive:raise ValueError('库内相对路径无效。')
 raw=Path(root)/p;reject_links(raw);target=raw.resolve()
 if not target.is_relative_to(Path(root).resolve()):raise ValueError('库内路径越界。')
 return str(target)

def snapshot(data,lib,app_root,tasks=None):
 root=Path(lib['path']);works=copy.deepcopy([w for w in data['works'] if w.get('library_id')==lib['id']]);ids={w['id'] for w in works};episodes=copy.deepcopy([e for e in data['episodes'] if e['work_id'] in ids])
 for w in works:
  for key in ('cover','banner'):
   value=w.get(key,'')
   if not value:continue
   source=Path(value);source=source if source.is_absolute() else Path(app_root)/source;reject_links(source)
   if source.is_file():
    digest=hashlib.sha256(source.read_bytes()).hexdigest();dest=root/DIRECTORY/'covers'/(digest+source.suffix.lower());reject_links(dest);dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():atomic_bytes(dest,source.read_bytes())
    w[key]=dest.relative_to(root).as_posix()
   else:w[key]=''
 for e in episodes:
  for f in e.get('files',[]):
   reject_links(f['path']);p=Path(f['path']).resolve()
   try:f['path']=p.relative_to(root.resolve()).as_posix();f['location']='library'
   except ValueError:f['location']='external'
 payload={'library':{k:v for k,v in lib.items() if k in ('id','label')},'works':works,'episodes':episodes,'tasks':tasks or []}
 return sanitize(payload)

@synchronized
def atomic_bytes(path,value):
 reject_links(path)
 temp=path.with_name('.'+uuid.uuid4().hex+'.tmp')
 try:temp.write_bytes(value);replace(temp,path)
 finally:temp.unlink(missing_ok=True)

@synchronized
def publish(root,payload):
 folder=Path(root)/DIRECTORY;reject_links(folder/'history');current=folder/'current.json'
 previous=load(current) if current.exists() else None
 fallback=copy.deepcopy(payload)
 for w in fallback['works']:
  for k in ('no_play_prompt','check_enabled','check_hours','checked_at','cover','banner'):w.pop(k,None)
 fallback['tasks']=[]
 # Independent source publication can survive a later full-metadata write failure.
 atomic(folder/'sources.json',encode(fallback))
 if previous==payload:return
 if previous is not None:atomic(folder/'history'/(uuid.uuid4().hex+'.json'),encode(previous))
 atomic(current,encode(payload))
 histories=sorted((folder/'history').glob('*.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
 for old in histories[3:]:sharing_retry(old.unlink)

def materialize(root,payload):
 result=copy.deepcopy(payload)
 for e in result['episodes']:
  for f in e.get('files',[]):
   if f.pop('location','external')=='library':f['path']=resolve(root,f['path'])
 for w in result['works']:
  for k,v in {'no_play_prompt':False,'check_enabled':False,'check_hours':24,'checked_at':0,'cover':'','banner':'','new_count':0}.items():w.setdefault(k,v)
  for k in ('cover','banner'):
   if w.get(k):w[k]=resolve(root,w[k])
 return result

# All Catalogue objects belonging to one application index share a write lease.
_shared={}
_frozen={}

def frozen(root):return bool(_frozen.get(os.path.normcase(str(Path(root).resolve())),0))

class SharedLease:
 def __init__(self,root,owner):
  self.key=(os.path.normcase(str(Path(root).resolve())),str(Path(owner).resolve()));self.closed=False
  row=_shared.get(self.key)
  if row is None:row=[Lease(root),0];_shared[self.key]=row
  row[1]+=1
 def close(self):
  if self.closed:return
  self.closed=True;row=_shared[self.key];row[1]-=1
  if not row[1]:row[0].close();del _shared[self.key]

from contextlib import contextmanager
@contextmanager
def freeze(root):
 key=os.path.normcase(str(Path(root).resolve()));_frozen[key]=_frozen.get(key,0)+1
 try:yield
 finally:
  _frozen[key]-=1
  if not _frozen[key]:del _frozen[key]

@synchronized
def publish_recovery(root,payload):
 """Publish an explicitly selected recovery, retaining corrupt original bytes."""
 folder=Path(root)/DIRECTORY;reject_links(folder/'history');current=folder/'current.json'
 if current.exists():
  raw=current.read_bytes()
  try:record=json.loads(raw)
  except (ValueError,UnicodeError):record=None
  if isinstance(record,dict) and record.get('format') is not None and record.get('format')!=FORMAT:raise ValueError('无法识别的存储库格式；原件保留。')
  try:load(current)
  except (ValueError,KeyError,TypeError):
   quarantine=folder/'quarantine'/(uuid.uuid4().hex+'.json');quarantine.parent.mkdir(parents=True,exist_ok=True);atomic_bytes(quarantine,raw)
   atomic(current,encode(payload))
 publish(root,payload)

_metadata_cache={}
@synchronized
def metadata_state(root):
 root=Path(root);current=root/DIRECTORY/'current.json';reject_links(current)
 try:
  info=current.stat();fingerprint=(info.st_mtime_ns,info.st_ctime_ns,info.st_size,info.st_ino)
 except FileNotFoundError:fingerprint=None
 key=str(current)
 stamp=(fingerprint,(root/DIRECTORY).exists())
 cached=_metadata_cache.get(key)
 if cached and cached[0]==stamp:return dict(cached[1])
 if fingerprint is None:
  result={'state':'corrupt','error':'存储库当前恢复资料缺失。'} if (root/DIRECTORY).exists() else {'state':'empty','error':''}
 else:
  try:value=load(current);result={'state':'valid','error':'','library_id':value['library']['id']}
  except (OSError,ValueError,KeyError,TypeError) as exc:result={'state':'unknown' if '格式' in str(exc) else 'corrupt','error':str(exc)}
 _metadata_cache[key]=(stamp,result)
 return dict(result)
