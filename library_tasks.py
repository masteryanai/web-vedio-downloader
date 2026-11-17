"""Portable, credential-free download task references for a storage library."""
from contextlib import ExitStack, contextmanager
import math
from pathlib import Path
import re
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import download_tasks as tasks

_SCALARS={'url','site','title','format_id','state','stage','library_title','library_episode'}
_NUMBERS={'created_at','finished_at','download_percent','downloaded_bytes','total_bytes'}

def _safe(path):
    path=Path(path).absolute()
    for part in (path,*path.parents):
        try:info=part.lstat()
        except FileNotFoundError:continue
        if part.is_symlink() or getattr(info,'st_file_attributes',0)&1024:
            raise ValueError('任务路径不能经过符号链接或目录联接。')
    return path.resolve()

def _id(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value):raise ValueError('无效下载任务编号。')
    return value

def _url(value):
    parts=urlsplit(value)
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('任务原网页地址无效。')
    if 'avcstreamdownload' in parts.path.lower() or parts.path.lower().endswith(('.m3u8','.mpd','.mp4','.ts')):raise ValueError('任务来源必须是原网页。')
    stable={'id','vid','bvid','aid','cid','season_id','episode_id','ep_id','p','v','tvid','albumid'}
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode([(k,v) for k,v in parse_qsl(parts.query) if k.lower() in stable]),''))

def _ref(value,root):
    path=_safe(value)
    return {'kind':'relative','path':path.relative_to(root).as_posix()} if path.is_relative_to(root) else {'kind':'external','path':str(path)}

def _path(ref,root):
    if not isinstance(ref,dict) or set(ref)!={'kind','path'} or not isinstance(ref['path'],str):raise ValueError('任务路径资料无效。')
    path=Path(ref['path'])
    if ref['kind']=='relative':
        if path.is_absolute() or path.drive or '..' in path.parts:raise ValueError('任务路径超出存储库。')
        resolved=_safe(root/path)
        if not resolved.is_relative_to(root):raise ValueError('任务路径超出存储库。')
        return resolved
    if ref['kind']=='external' and path.is_absolute():return _safe(path)
    raise ValueError('任务路径资料无效。')

def _public(task):
    result={key:task[key] for key in _SCALARS if isinstance(task.get(key),str)}
    for key in _NUMBERS:
        value=task.get(key)
        if type(value) in (int,float) and math.isfinite(value):result[key]=value
    for key,fields in [('format',{'width','height','fps','vcodec','acodec'}),('quality_policy',{'height','strict','kind'})]:
        value=task.get(key)
        if isinstance(value,dict):result[key]={k:v for k,v in value.items() if k in fields and type(v) in (str,int,float,bool) and (type(v) is not float or math.isfinite(v))}
    result['url']=_url(result.get('url',''))
    return result

def snapshot_tasks(libid,root,context):
    root=_safe(root)
    works={w['id'] for w in context.get('works',[]) if w.get('library_id')==libid}
    episodes={e['id']:e for e in context.get('episodes',[]) if e.get('work_id') in works}
    ids={t:eid for eid,e in episodes.items() for t in e.get('task_ids',[e.get('task_id')]) if t}
    # Legacy downloads may predate episode binding; their output still belongs to this root.
    for task in tasks.records():
        if task['id'] not in ids and not task.get('library_episode') and task['state'] not in tasks.TERMINAL:
            if _safe(task['output']).is_relative_to(root):ids[task['id']]=None
    rows=[]
    for taskid,eid in ids.items():
        try:task=tasks.load_record(taskid)
        except FileNotFoundError:continue
        row=_public(task)
        if eid:row['library_episode']=eid
        row.update(id=taskid,output=_ref(task['output'],root),cache=_ref(tasks.work_path(task),root))
        result=task.get('result')
        if isinstance(result,dict) and isinstance(result.get('path'),str):row['result']={'path':_ref(result['path'],root)}
        rows.append(row)
    return rows

@contextmanager
def restore_transaction(rows,root,identity_map):
    root=_safe(root);prepared=[];mapping={};seen=set();renames=[]
    for row in rows:
        if not isinstance(row,dict):raise ValueError('任务资料无效。')
        old=_id(row.get('id'))
        if old in seen:raise ValueError('任务编号重复。')
        seen.add(old);new=_id(identity_map.get(old,old)); output=_path(row.get('output'),root)
        cache=_path(row.get('cache'),root)
        expected=output/('.video-downloader-'+old)
        if cache!=expected:raise ValueError('任务缓存关联无效。')
        task=_public(row)
        if any(not isinstance(task.get(k),str) for k in ('url','site','title','format_id','state')):raise ValueError('下载任务信息不完整。')
        if task['state'] not in {'queued','running','paused','failed','completed','cancelled'}:raise ValueError('下载任务状态无效。')
        try:existing=tasks.load_record(new)
        except FileNotFoundError:existing=None
        if existing and _safe(existing['output'])!=output:
            # A genuine relocation has lost its old output; a live second root is a clone.
            if old not in identity_map and Path(existing['output']).exists():new=uuid.uuid4().hex
        if new in mapping.values():raise ValueError('恢复任务编号冲突。')
        task.update(id=new,output=str(output))
        if task.get('library_episode'):task['library_episode']=identity_map.get(task['library_episode'],task['library_episode'])
        if 'result' in row:
            result=row['result']
            if not isinstance(result,dict) or set(result)!={'path'}:raise ValueError('任务成品资料无效。')
            task['result']={'path':str(_path(result['path'],root))}
            if task['state'] not in tasks.TERMINAL:task.pop('result',None)
        if task['state'] not in tasks.TERMINAL:task.update(state='paused',stage='等待继续；恢复的缓存将先验证来源',restored_cache=True)
        if row['output']['kind']=='external':task.update(stage='库外任务输出待重新定位；现有文件已保留',restore_requires_relocation=True)
        if cache.is_relative_to(root) and cache.exists():
            for child in cache.rglob('*'):_safe(child)
        destination=output/('.video-downloader-'+new)
        if new!=old and cache.is_relative_to(root) and cache.exists():
            if destination.exists():raise ValueError('恢复任务缓存已存在。')
            # Validate the full cache tree before any rename/write; never traverse links.
            for child in cache.rglob('*'):_safe(child)
            renames.append((cache,destination))
        mapping[old]=new;prepared.append(task)
    with ExitStack() as stack:
        for taskid in sorted(set(mapping)|set(mapping.values())):stack.enter_context(tasks.lock(taskid))
        done=[];original={}
        try:
            for src,dst in renames:src.rename(dst);done.append((src,dst))
            for task in prepared:
                path=tasks.task_file(task['id']);original[path]=path.read_bytes() if path.exists() else None
                tasks.save(task)
            yield mapping
        except BaseException:
            for path,data in original.items():
                if data is None:path.unlink(missing_ok=True)
                else:tasks.write_checkpoint_bytes(path,data)
            for src,dst in reversed(done):dst.rename(src)
            raise


def restore_tasks(rows,root,identity_map):
    with restore_transaction(rows,root,identity_map) as mapping:
        return mapping
