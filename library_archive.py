"""Self describing, validated library records. No network or media deletion."""
import copy,hashlib,json,os,uuid
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode

FORMAT=1
DIRECTORY='.wvd-library'

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')

def web_url(value):
 try:
  p=urlsplit(value)
  if p.scheme not in ('http','https') or not p.hostname:return ''
  if p.path.lower().endswith(('.m3u8','.mp4','.ts','.mpd')):return ''
  query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not any(s in k.lower() for s in ('token','auth','cookie','signature','session','secret','expire','credential','password'))]
  return urlunsplit((p.scheme,p.hostname+(':'+str(p.port) if p.port else ''),p.path,urlencode(query),''))
 except ValueError:return ''

def sanitize(value):
 if isinstance(value,dict):return {k:(web_url(v) if k.endswith('url') and isinstance(v,str) else sanitize(v)) for k,v in value.items() if not any(s in k.lower() for s in ('cookie','token','password','headers','authorization'))}
 if isinstance(value,list):return [sanitize(v) for v in value]
 return value

def encode(payload):return {'format':FORMAT,'payload':payload,'sha256':hashlib.sha256(canonical(payload)).hexdigest()}

def load(path):
 record=json.loads(Path(path).read_text(encoding='utf-8'))
 if record.get('format')!=FORMAT:raise ValueError('无法识别的存储库格式；原件保留。')
 payload=record['payload']
 if hashlib.sha256(canonical(payload)).hexdigest()!=record.get('sha256'):raise ValueError('存储库资料校验失败；原件保留。')
 if not isinstance(payload.get('library'),dict) or not isinstance(payload.get('works'),list) or not isinstance(payload.get('episodes'),list):raise ValueError('存储库资料结构无效。')
 return payload

def atomic(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name('.'+uuid.uuid4().hex+'.tmp')
 try:
  with temp.open('wb') as out:out.write(canonical(value));out.flush();os.fsync(out.fileno())
  os.replace(temp,path)
 finally:temp.unlink(missing_ok=True)

_held_names=set()

class Lease:
 def __init__(self,root):
  self.file=None;self.handle=None;self.name=None
  if os.name=='nt':
   import ctypes
   name='Global\\WVDLibrary-'+hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode()).hexdigest()
   if name in _held_names:raise OSError('另一实例正在写入存储库。')
   create=ctypes.windll.kernel32.CreateMutexW;create.restype=ctypes.c_void_p
   self.handle=create(None,False,name)
   if not self.handle:raise ctypes.WinError()
   wait=ctypes.windll.kernel32.WaitForSingleObject;wait.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
   if wait(self.handle,0) not in (0,0x80):
    self.close();raise OSError('另一程序正在写入存储库。')
   self.name=name;_held_names.add(name)
  else:
   import fcntl
   folder=Path(root)/DIRECTORY;folder.mkdir(exist_ok=True);self.file=(folder/'writer.lock').open('a+b')
   try:fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BaseException:self.file.close();raise
 def close(self):
  if self.name:_held_names.discard(self.name);self.name=None
  if self.handle:
   import ctypes
   release=ctypes.windll.kernel32.ReleaseMutex;release.argtypes=[ctypes.c_void_p];release(self.handle)
   close=ctypes.windll.kernel32.CloseHandle;close.argtypes=[ctypes.c_void_p];close(self.handle);self.handle=None
  if self.file and not self.file.closed:self.file.close()
 def __del__(self):
  try:self.close()
  except Exception:pass

def resolve(root,value):
 p=Path(value)
 if p.is_absolute():raise ValueError('库内相对路径无效。')
 raw=Path(root)/p
 for part in (raw,*raw.parents):
  if part.is_symlink() or part.exists() and getattr(part.stat(),'st_file_attributes',0)&1024:raise ValueError('库内路径不能经过链接。')
 target=raw.resolve()
 if not target.is_relative_to(Path(root).resolve()):raise ValueError('库内路径越界。')
 for part in (target,*target.parents):
  if part.is_symlink() or part.exists() and getattr(part.stat(),'st_file_attributes',0)&1024:raise ValueError('库内路径不能经过链接。')
 return str(target)

def snapshot(data,lib,app_root,tasks=None):
 root=Path(lib['path']);works=copy.deepcopy([w for w in data['works'] if w.get('library_id')==lib['id']]);ids={w['id'] for w in works};episodes=copy.deepcopy([e for e in data['episodes'] if e['work_id'] in ids])
 for w in works:
  for key in ('cover','banner'):
   value=w.get(key,'')
   if not value:continue
   source=Path(value);source=source if source.is_absolute() else Path(app_root)/source
   if source.is_file():
    digest=hashlib.sha256(source.read_bytes()).hexdigest();dest=root/DIRECTORY/'covers'/(digest+source.suffix.lower());dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():atomic_bytes(dest,source.read_bytes())
    w[key]=dest.relative_to(root).as_posix()
   else:w[key]=''
 for e in episodes:
  for f in e.get('files',[]):
   p=Path(f['path']).resolve()
   try:f['path']=p.relative_to(root.resolve()).as_posix();f['location']='library'
   except ValueError:f['location']='external'
 payload={'library':{k:v for k,v in lib.items() if k in ('id','label')},'works':works,'episodes':episodes,'tasks':tasks or []}
 return sanitize(payload)

def atomic_bytes(path,value):
 temp=path.with_name('.'+uuid.uuid4().hex+'.tmp')
 try:temp.write_bytes(value);os.replace(temp,path)
 finally:temp.unlink(missing_ok=True)

def publish(root,payload):
 folder=Path(root)/DIRECTORY;current=folder/'current.json'
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
 for old in histories[3:]:old.unlink()

def materialize(root,payload):
 result=copy.deepcopy(payload)
 for e in result['episodes']:
  for f in e.get('files',[]):
   if f.pop('location','external')=='library':f['path']=resolve(root,f['path'])
 for w in result['works']:
  for k in ('cover','banner'):
   if w.get(k):w[k]=resolve(root,w[k])
 return result
