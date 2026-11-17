"""Verified whole-library relocation; callers pause the queue before entering."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import uuid

import download_tasks as tasks
from media_library import safe_folder as _catalogue_folder

def safe_folder(path):
    path=Path(path).expanduser()
    from library_archive import reject_links
    reject_links(path)
    return _catalogue_folder(path)

_JOURNAL='.wvd-transfer'
_CHUNK=1024*1024

class _Cancelled(Exception):pass

def _check(cancel):
    if cancel():raise _Cancelled('迁移已取消；源库和已复制资料均已保留。')

def _hash(path,cancel):
    safe_folder(path)
    digest=hashlib.sha256()
    with path.open('rb') as handle:
        while block:=handle.read(_CHUNK):
            _check(cancel);digest.update(block)
    return digest.hexdigest()

def _tree(root,cancel):
    files={};directories=[]
    def visit(folder):
        _check(cancel)
        for child in folder.iterdir():
            safe_folder(child)
            mode=child.stat().st_mode
            relative=child.relative_to(root).as_posix()
            if stat.S_ISDIR(mode):directories.append(relative);visit(child)
            elif stat.S_ISREG(mode):files[relative]={'size':child.stat().st_size,'sha256':_hash(child,cancel)}
            else:raise ValueError('存储库包含不支持的特殊文件：'+str(child))
    visit(root)
    return files,directories

def preview_move(store,libid,destination,cancel=lambda:False,progress=lambda data:None):
    """Read-only stat scan; migration repeats full hash validation."""
    source=safe_folder(next(v['path'] for v in store.read()['libraries'] if v['id']==libid))
    dest=safe_folder(Path(destination).absolute())
    if source==dest or source.is_relative_to(dest) or dest.is_relative_to(source):
        raise ValueError('源库与目标目录不能相同或互相嵌套。')
    result={'source':str(source),'destination':str(dest),'total_files':0,'total_bytes':0,
            'scope':'源库目录内全部文件（视频、字幕、附带文件、任务缓存与恢复资料）'}
    def visit(folder):
        _check(cancel)
        for child in folder.iterdir():
            _check(cancel);safe_folder(child);info=child.stat()
            if stat.S_ISDIR(info.st_mode):visit(child)
            elif stat.S_ISREG(info.st_mode):
                result['total_files']+=1;result['total_bytes']+=info.st_size
                progress(dict(result,phase='scan',path=str(child)))
            else:raise ValueError('存储库包含不支持的特殊文件：'+str(child))
    visit(source)
    _check(cancel)
    return result

def _inactive(store,libid):
    data=store.read();works={w['id'] for w in data['works'] if w.get('library_id')==libid}
    root=safe_folder(next(v['path'] for v in data['libraries'] if v['id']==libid))
    ids={t for e in data['episodes'] if e.get('work_id') in works for t in [e.get('task_id'),*e.get('task_ids',[])] if t}
    for record in tasks.records():
        if Path(record['output']).resolve().is_relative_to(root):
            safe_folder(record['output']);ids.add(record['id'])
    for taskid in sorted(ids):
        # Check ownership without retaining the lock across restore_tasks().
        with tasks.lock(taskid):
            try:record=tasks.load_record(taskid)
            except FileNotFoundError:continue
            if record['state'] in ('running','queued'):raise ValueError('请先暂停存储库的下载及排队任务，再迁移。')

def move_library(store,libid,destination,cancel=lambda:False,progress=lambda data:None):
    """Return a result dict, including retained paths on every failure/cancellation.

    Progress: phase (scan/copy/verify/mount/cleanup), bytes, total_bytes, files,
    total_files, path. Source cleanup never follows links or removes changed files.
    """
    source=None;dest=Path(destination).absolute();mounted=False
    result={'success':False,'cancelled':False,'source':None,'destination':str(dest),
            'error':'','retained_source':[],'files':0,'bytes':0}
    try:
        source=safe_folder(next(v['path'] for v in store.read()['libraries'] if v['id']==libid))
        dest=safe_folder(dest);result.update(source=str(source),destination=str(dest))
        if source==dest or source.is_relative_to(dest) or dest.is_relative_to(source):raise ValueError('源库与目标目录不能相同或互相嵌套。')
        for lib in store.read()['libraries']:
            if lib['id']==libid:continue
            other=safe_folder(lib['path'])
            if any(root==other or root.is_relative_to(other) or other.is_relative_to(root) for root in (source,dest)):
                raise ValueError('迁移目录不能与其他存储库相同或嵌套：'+str(other))
        if (source/_JOURNAL).exists():raise ValueError('源库存在未完成迁移标记，请先检查保留的迁移目录。')
        if not source.is_dir():raise ValueError('源库存储目录不可用。')
        if not store.library_status(libid)['writable']:raise ValueError(store.library_status(libid)['error'])
        _inactive(store,libid);_check(cancel)
        store.persist_library(libid)
        with store.freeze_library(libid):
            _inactive(store,libid)
            progress({'phase':'scan','path':str(source),'bytes':0,'total_bytes':0,'files':0,'total_files':0})
            files,dirs=_tree(source,cancel);total=sum(row['size'] for row in files.values())
            result.update(total_bytes=total,total_files=len(files))
            journal=dest/_JOURNAL
            if dest.exists() and any(dest.iterdir()):
                safe_folder(journal)
                try:previous=json.loads((journal/'manifest.json').read_text(encoding='utf-8'))
                except (OSError,ValueError):raise ValueError('目标目录非空且不是本次迁移的保留目录，未覆盖任何文件。') from None
                if previous.get('source')!=str(source) or previous.get('library_id')!=libid:raise ValueError('目标目录属于其他迁移，未覆盖任何文件。')
                # Only verified completed files from the previous run are reusable.
                existing,_=_tree(dest,cancel)
                for rel,row in existing.items():
                    if rel.startswith(_JOURNAL+'/'):continue
                    if previous.get('files',{}).get(rel)!=row or files.get(rel)!=row:raise ValueError('目标保留目录存在未知或已改变的文件，未覆盖。')
            dest.mkdir(parents=True,exist_ok=True);safe_folder(dest)
            if shutil.disk_usage(dest).free<total:raise OSError('目标磁盘空间不足；源库已保留。')
            journal.mkdir(exist_ok=True);safe_folder(journal)
            (journal/'manifest.json').write_text(json.dumps({'source':str(source),'library_id':libid,'files':files},ensure_ascii=False),encoding='utf-8')
            for rel in dirs:safe_folder(dest/rel).mkdir(parents=True,exist_ok=True)
            copied=0
            for rel,row in files.items():
                _check(cancel);src=safe_folder(source/rel);target=safe_folder(dest/rel)
                target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists():
                    temporary=journal/(uuid.uuid4().hex+'.part')
                    try:
                        with src.open('rb') as reader,temporary.open('xb') as writer:
                            while block:=reader.read(_CHUNK):
                                _check(cancel);writer.write(block)
                                progress({'phase':'copy','path':rel,'bytes':copied+writer.tell(),'total_bytes':total,'files':result['files'],'total_files':len(files)})
                        if _hash(temporary,cancel)!=row['sha256']:raise OSError('复制校验失败：'+rel)
                        if target.exists():raise ValueError('复制期间目标文件已出现，停止覆盖：'+rel)
                        temporary.rename(target)
                        shutil.copystat(src,target,follow_symlinks=False)
                    finally:temporary.unlink(missing_ok=True)
                if _hash(target,cancel)!=row['sha256'] or _hash(src,cancel)!=row['sha256']:raise OSError('源文件或目标文件在复制期间改变：'+rel)
                copied+=row['size'];result.update(bytes=copied,files=result['files']+1)
            progress({'phase':'verify','path':str(dest),'bytes':copied,'total_bytes':total,'files':len(files),'total_files':len(files)})
            for rel,row in files.items():
                if _hash(dest/rel,cancel)!=row['sha256']:raise OSError('目标文件最终校验失败：'+rel)
            _check(cancel);_inactive(store,libid)
            preview=store.probe_library(dest)
            if preview['state']!='valid' or preview['library_id']!=libid:raise ValueError('目标库恢复资料未通过验证。')
            progress({'phase':'mount','path':str(dest),'bytes':copied,'total_bytes':total,'files':len(files),'total_files':len(files)})
            for rel,row in files.items():
                if _hash(dest/rel,cancel)!=row['sha256']:raise OSError('目标文件在提交前改变：'+rel)
            _check(cancel)
            store.mount_library(dest,mode='relocate')
            if not any(v['id']==libid and safe_folder(v['path'])==dest for v in store.read()['libraries']):raise OSError('目标库位置未提交，源文件已保留。')
            mounted=True;result['success']=True
            # Mount may update metadata/tasks in the destination. Never delete source
            # until that public transaction has completed successfully.
            for rel,row in files.items():
                try:
                    path=safe_folder(source/rel)
                    if path.is_file() and _hash(path,lambda:False)==row['sha256']:path.unlink()
                    elif path.exists():result['retained_source'].append(str(path))
                except (OSError,ValueError):result['retained_source'].append(str(source/rel))
            for rel in sorted(dirs,key=lambda value:len(Path(value).parts),reverse=True):
                try:safe_folder(source/rel).rmdir()
                except (OSError,ValueError):pass
            try:source.rmdir()
            except OSError:pass
            # Report additions and changed files left behind, without traversing links.
            if source.exists():result['retained_source'].append(str(source))
            shutil.rmtree(journal)
    except Exception as exc:
        result['cancelled']=isinstance(exc,_Cancelled);result['error']=str(exc)
        if source is not None and source.exists():result['retained_source'].append(str(source))
        if mounted:result['success']=True
    return result
