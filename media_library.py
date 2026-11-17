"""Portable catalogue. Media paths are references; catalogue removal never deletes media."""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
from runtime import app_data_dir

_gate=threading.RLock()

def identifier():return uuid.uuid4().hex

def component(value):
    value=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',value).strip().rstrip('. ')[:100]
    if not value or re.match(r'(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)',value):value='影片_'+identifier()[:8]
    return value

def safe_folder(path):
    path=Path(path).expanduser()
    if not path.is_absolute():raise ValueError('请选择绝对路径。')
    for part in (path,*path.parents):
        if part.is_symlink() or part.exists() and getattr(part.stat(),'st_file_attributes',0)&1024:raise ValueError('存储路径不能经过目录联接或符号链接。')
    return path.resolve()

def source_key(site,key,variant=''):return site+'|'+str(key)+('|线路:'+str(variant) if variant else '')


class MetadataSaveError(OSError):
    """Media/global index are retained; library recovery metadata needs retry."""


class Catalogue:
    task_snapshot_provider=None
    task_restore_handler=None

    def __init__(self):
        self.path=app_data_dir()/'library'/'catalogue.json'
        self._leases={};self._statuses={}

    def close(self):
        for lease in self._leases.values():lease.close()
        self._leases.clear()

    def __del__(self):
        try:self.close()
        except Exception:pass

    def library_status(self,key):
        lib=next(v for v in self.read()['libraries'] if v['id']==key)
        root=Path(lib['path'])
        from library_archive import frozen
        if frozen(root):return {'writable':False,'state':'busy','error':'存储库正在迁移，请稍后重试。','pending':False}
        if not root.is_dir():return {'writable':False,'state':'offline','error':'存储库离线。','pending':False}
        if not root.stat().st_mode & 0o222:return {'writable':False,'state':'read_only','error':'存储库只读。','pending':False}
        if key not in self._leases:
            try:
                from library_archive import SharedLease
                self._leases[key]=SharedLease(root,self.path)
            except OSError as exc:return {'writable':False,'state':'read_only','error':'存储库只读或正被另一程序使用：'+str(exc),'pending':False}
        return self._statuses.get(key,{'writable':True,'state':'ready','error':'','pending':False})

    def persist_library(self,key,data=None):
        from library_archive import snapshot,publish
        data=data if data is not None else self.read();lib=next(v for v in data['libraries'] if v['id']==key)
        status=self.library_status(key)
        if not status['writable']:raise ValueError(status['error'])
        try:
            provider=self.task_snapshot_provider
            if provider is None:
                try:
                    from library_tasks import snapshot_tasks
                    provider=snapshot_tasks
                except ImportError:pass
            tasks=provider(key,Path(lib['path']),data) if provider else []
            publish(lib['path'],snapshot(data,lib,app_data_dir(),tasks))
            self._statuses[key]={'writable':True,'state':'ready','error':'','pending':False};return True
        except (OSError,ValueError) as exc:
            self._statuses[key]={'writable':True,'state':'pending','error':'视频已保存，恢复资料未保存，可重试：'+str(exc),'pending':True}
            raise MetadataSaveError(self._statuses[key]['error']) from exc

    def retry_persist(self,key):return self.persist_library(key)

    @contextmanager
    def freeze_library(self,key):
        from library_archive import freeze
        lib=next(v for v in self.read()['libraries'] if v['id']==key)
        status=self.library_status(key)
        if not status['writable']:raise ValueError(status['error'])
        with freeze(lib['path']):yield

    def probe_library(self,path,recovery='current'):
        from library_archive import DIRECTORY,load,materialize
        root=safe_folder(path);folder=root/DIRECTORY
        preview={'state':'empty','library_id':None,'label':root.name,'works':0,'episodes':0,'missing_files':[], 'histories':[],'conflict':None,'writable':os.access(root,os.W_OK),'error':'','sources_available':False,'needs_online_confirmation':recovery=='sources'}
        for history in sorted((folder/'history').glob('*.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True):
            try:
                value=load(history);preview['histories'].append({'id':history.stem,'works':len(value['works'])})
            except (OSError,ValueError,KeyError):pass
        try:load(folder/'sources.json');preview['sources_available']=True
        except (OSError,ValueError,KeyError):pass
        target=folder/('current.json' if recovery=='current' else 'sources.json' if recovery=='sources' else 'history/'+recovery.removeprefix('history:')+'.json')
        if recovery not in ('current','sources') and not re.fullmatch('[a-f0-9]{32}',recovery.removeprefix('history:')):raise ValueError('无效历史标识。')
        if not target.exists():
            if folder.exists():preview.update(state='corrupt',error='恢复资料不存在。')
            return preview
        try:
            payload=load(target);value=materialize(root,payload)
            preview.update(state='valid',library_id=value['library']['id'],label=value['library'].get('label',root.name),works=len(value['works']),episodes=len(value['episodes']))
            preview['missing_files']=[f['path'] for e in value['episodes'] for f in e.get('files',[]) if not Path(f['path']).is_file()]
            old=next((v for v in self.read()['libraries'] if v['id']==preview['library_id'] and os.path.normcase(v['path'])!=os.path.normcase(str(root))),None)
            if old:preview['conflict']={'id':old['id'],'path':old['path']}
        except (OSError,ValueError,KeyError,TypeError) as exc:
            preview.update(state='unknown' if '格式' in str(exc) else 'corrupt',error=str(exc))
        return preview

    def mount_library(self,path,mode='restore',recovery='current'):
        from library_archive import DIRECTORY,load,materialize
        if mode not in ('restore','relocate','clone'):raise ValueError('无效挂载方式。')
        root=safe_folder(path);preview=self.probe_library(root,recovery)
        if preview['state']!='valid':raise ValueError(preview['error'] or '没有可恢复的存储库资料。')
        if mode=='clone' and any(os.path.normcase(v['path'])==os.path.normcase(str(root)) for v in self.read()['libraries']):raise ValueError('已挂载的原库不能原地成为独立副本，请先复制到另一目录。')
        if preview['conflict'] and mode=='restore':raise ValueError('同一存储库已挂载，请选择搬家或独立副本。')
        target=root/DIRECTORY/('current.json' if recovery=='current' else 'sources.json' if recovery=='sources' else 'history/'+recovery.removeprefix('history:')+'.json')
        value=materialize(root,load(target));oldid=value['library']['id'];mapping={v['id']:v['id'] for v in [value['library'],*value['works'],*value['episodes']]}
        if mode=='clone':
            mapping={v['id']:identifier() for v in [value['library'],*value['works'],*value['episodes']]}
            for w in value['works']:
                w['id']=mapping[w['id']];w['library_id']=mapping[oldid]
                for source in w.get('sources',[]):source['mapping']={k:mapping.get(v,v) for k,v in source.get('mapping',{}).items()}
            for e in value['episodes']:e['id']=mapping[e['id']];e['work_id']=mapping[e['work_id']]
            value['library']['id']=mapping[oldid]
        libid=value['library']['id'];value['library']['path']=str(root)
        # Establish physical write access before a task bridge can touch caches.
        from library_archive import SharedLease,frozen
        can_write=False
        if recovery not in ('current','sources') and self.probe_library(root)['state']=='unknown':raise ValueError('无法识别的存储库格式；原件保留。')
        try:
            if frozen(root) or not root.stat().st_mode & 0o222:raise OSError('存储库只读或正在迁移。')
            if mode=='relocate' and libid in self._leases:
                oldlease=self._leases.pop(libid);oldlease.close()
            if libid not in self._leases:self._leases[libid]=SharedLease(root,self.path)
            can_write=True
            if recovery!='sources':self._statuses.pop(libid,None)
        except OSError:
            if mode=='clone' or recovery not in ('current','sources'):raise ValueError('存储库不可写，未恢复任务或修改缓存。')
        handler=self.task_restore_handler
        if handler is None:
            try:
                from library_tasks import restore_tasks
                handler=restore_tasks
            except ImportError:pass
        for task in value.get('tasks',[]):
            if task.get('id'):mapping.setdefault(task['id'],identifier() if mode=='clone' else task['id'])
        if handler and can_write and recovery!='sources':
            remap=handler(value.get('tasks',[]),root,mapping) or {}
        else:remap={}
        for e in value['episodes']:
            if not can_write:continue
            e['task_ids']=[remap[t] for t in e.get('task_ids',[]) if t in remap];e['task_id']=remap.get(e.get('task_id'));e['synced_tasks']=[]
            if e['task_id']:e['status']='已暂停'
            elif e.get('status') in ('排队中','处理中'):e['status']='待继续'
        with self.change(persist=False) as data:
            data['libraries']=[v for v in data['libraries'] if v['id']!=libid]+[value['library']]
            previous={w['id'] for w in data['works'] if w.get('library_id')==libid}
            conflicts={w['id'] for w in data['works'] if w.get('library_id')!=libid}&{w['id'] for w in value['works']}
            if conflicts:raise ValueError('作品身份与另一存储库冲突，请使用独立副本。')
            data['works']=[w for w in data['works'] if w.get('library_id')!=libid]+value['works']
            data['episodes']=[e for e in data['episodes'] if e['work_id'] not in previous]+value['episodes']
        if recovery=='sources':
            self._statuses[libid]={'writable':False,'state':'needs_online_confirmation','error':'请重新解析来源并确认匹配。','pending':False}
        elif mode=='clone' or recovery!='current':
            # Explicit history recovery may replace a damaged current record.
            from library_archive import snapshot,publish_recovery
            if not self.library_status(libid)['writable']:raise ValueError('存储库不可写。')
            context=self.read();provider=self.task_snapshot_provider
            if provider is None:
                try:
                    from library_tasks import snapshot_tasks
                    provider=snapshot_tasks
                except ImportError:pass
            task_rows=provider(libid,root,context) if provider else value.get('tasks',[])
            payload=snapshot(context,value['library'],app_data_dir(),task_rows)
            publish_recovery(root,payload)
        return libid

    def confirm_source_restore(self,key):
        """Caller has verified source matches and obtained user confirmation."""
        from library_archive import snapshot,publish_recovery
        state=self._statuses.get(key,{})
        if state.get('state')!='needs_online_confirmation':raise ValueError('该存储库没有待确认的来源恢复。')
        data=self.read();lib=next(v for v in data['libraries'] if v['id']==key)
        # Temporarily remove the confirmation gate to check the physical lease.
        self._statuses.pop(key,None)
        try:
            status=self.library_status(key)
            if not status['writable']:raise ValueError(status['error'])
            publish_recovery(lib['path'],snapshot(data,lib,app_data_dir()))
            self._statuses[key]={'writable':True,'state':'ready','error':'','pending':False}
            return True
        except BaseException:
            self._statuses[key]=state
            raise

    def read(self):
        with _gate:
            if not self.path.exists():return {'schema':1,'libraries':[],'works':[],'episodes':[]}
            data=json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('schema') not in (1,2) or any(not isinstance(data.get(k),list) for k in ('libraries','works','episodes')):raise ValueError('影片库索引损坏，原文件已保留，请查看故障记录。')
            return data

    @contextmanager
    def change(self,persist=True):
        from diagnostics import file_lock
        with _gate:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with file_lock(self.path.parent):
                data=self.read();before=copy.deepcopy(data);yield data
                affected=[]
                for lib in data['libraries']:
                    key=lib['id'];oldlib=next((v for v in before['libraries'] if v['id']==key),None)
                    def section(value):
                        works=[w for w in value['works'] if w.get('library_id')==key];ids={w['id'] for w in works}
                        return works,[e for e in value['episodes'] if e['work_id'] in ids]
                    if persist and oldlib is not None and section(data)!=section(before):
                        preview=self.probe_library(lib['path'])
                        if preview['state'] in ('unknown','corrupt'):raise ValueError(preview['error'])
                        status=self.library_status(key)
                        if not status['writable']:raise ValueError(status['error'])
                        affected.append(key)
                    elif persist and oldlib is None:affected.append(key)
                if data==before:return
                data['schema']=2
                temp=self.path.with_name('catalogue-'+identifier()+'.tmp')
                try:temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(self.path)
                finally:temp.unlink(missing_ok=True)
                for key in affected:self.persist_library(key,data)

    def library(self,path,label=''):
        root=safe_folder(path)
        if root==Path(root.anchor):raise ValueError('请选择磁盘下的专用影片目录。')
        if (root/'.wvd-library').exists():return self.mount_library(root)
        root.mkdir(parents=True,exist_ok=True)
        with self.change() as data:
            old=next((v for v in data['libraries'] if os.path.normcase(v['path'])==os.path.normcase(str(root))),None)
            if old:return old['id']
            row={'id':identifier(),'path':str(root),'label':label.strip() or root.name};data['libraries'].append(row);return row['id']

    def remove_library(self,key):
        with self.change() as data:
            ids={w['id'] for w in data['works'] if w.get('library_id')==key}
            data['libraries']=[v for v in data['libraries'] if v['id']!=key]
            data['works']=[w for w in data['works'] if w['id'] not in ids]
            data['episodes']=[e for e in data['episodes'] if e['work_id'] not in ids]
        lease=self._leases.pop(key,None)
        if lease:lease.close()

    def ingest(self,collection,title,season,library_id=None,merge=None,mapping=None):
        """Explicit merge requires a source-entry -> existing episode mapping."""
        if not title.strip():raise ValueError('请填写作品名。')
        entries=collection['entries'];key=source_key(collection['site'],collection['key'],collection.get('variant',''))
        with self.change() as data:
            work=next((w for w in data['works'] if (not library_id or w.get('library_id')==library_id) and any(s['key']==key for s in w['sources'])),None)
            if merge:
                work=next(w for w in data['works'] if w['id']==merge)
                if work['kind']!=collection.get('kind','video'):raise ValueError('图文作品应独立入库，不能混入影片季。')
                if not mapping or any(e['key'] not in mapping for e in entries):raise ValueError('跨来源合并必须逐集确认对应关系。')
                targets=[v for v in mapping.values() if v!='__new__']
                if len(set(targets))!=len(targets):raise ValueError('同一来源的多个选集不能重复映射到同一集。')
            if work is None:
                work={'id':identifier(),'title':title.strip(),'season':season.strip(),'kind':collection.get('kind','video'),
                      'library_id':library_id,'folder_name':component(title)+(('/'+component(season)) if season else ''),
                      'sources':[],'no_play_prompt':False,'check_enabled':False,'check_hours':24,'checked_at':0,
                      'cover':'','banner':'','cover_url':collection.get('cover',''),'banner_url':collection.get('banner',''),
                      'created_at':time.time(),'new_count':0};data['works'].append(work)
                if any(w['id']!=work['id'] and w['folder_name']==work['folder_name'] for w in data['works']):work['folder_name']+=' ['+work['id'][:8]+']'
            if library_id and work.get('library_id') and work['library_id']!=library_id:raise ValueError('本季已指定存储库，请通过迁移操作更换。')
            if library_id:work['library_id']=library_id
            source=next((s for s in work['sources'] if s['key']==key),None)
            if source is None:
                source={'key':key,'site':collection['site'],'url':collection['url'],'work_key':collection['key'],'variant':collection.get('variant',''),'mapping':{}};work['sources'].append(source)
            source['url']=collection['url']
            added=[]
            for entry in entries:
                existing=source['mapping'].get(entry['key'])
                ep=next((e for e in data['episodes'] if e['id']==existing and e['work_id']==work['id']),None)
                if merge:
                    target=mapping[entry['key']]
                    ep=next((e for e in data['episodes'] if e['id']==target and e['work_id']==work['id']),None)
                    if ep is None and target!='__new__':raise ValueError('选集映射不属于目标季。')
                if ep is None:
                    ep={'id':identifier(),'work_id':work['id'],'label':entry['title'],'sources':[],'files':[],'task_id':None,'status':'未下载','number':len(source['mapping'])+1}
                    data['episodes'].append(ep);added.append(ep['id'])
                source['mapping'][entry['key']]=ep['id']
                address={'source':key,'key':entry['key'],'site':collection['site'],'url':entry['url']}
                ep['sources']=[s for s in ep['sources'] if s['source']!=key]+[address]
            work['checked_at']=time.time();work['new_count']=len(added) if source.get('seen') else 0;source['seen']=True
            return work['id'],[source['mapping'][e['key']] for e in entries]

    def patch_work(self,key,**values):
        allowed={'no_play_prompt','check_enabled','check_hours','cover','banner','default_source','new_count'}
        if not set(values)<=allowed:raise ValueError('不支持修改该字段。')
        for name in ('cover','banner'):
            if values.get(name):
                path=Path(values[name])
                if path.is_absolute():
                    try:values[name]=path.relative_to(app_data_dir()).as_posix()
                    except ValueError:values[name]=str(path)
        with self.change() as data:next(w for w in data['works'] if w['id']==key).update(values)

    def episode(self,key):return next(e for e in self.read()['episodes'] if e['id']==key)
    def work(self,key):return next(w for w in self.read()['works'] if w['id']==key)

    def output(self,key):
        data=self.read();work=next(w for w in data['works'] if w['id']==key)
        lib=next((v for v in data['libraries'] if v['id']==work.get('library_id')),None)
        if lib is None:raise ValueError('请先为本季选择存储库。')
        status=self.library_status(lib['id'])
        if not status['writable']:raise ValueError(status['error'])
        root=safe_folder(lib['path'])
        if not root.is_dir():raise ValueError('存储库离线或目录不存在，请先重新连接，不会自动换盘。')
        folder=safe_folder(root/work['folder_name'])
        if not folder.is_relative_to(root):raise ValueError('影片目录越界。')
        if shutil.disk_usage(root).free<100*1024**2:raise ValueError('存储库可用空间不足100 MiB，请清理或明确迁移。')
        folder.mkdir(parents=True,exist_ok=True);return folder

    def file_state(self,episode,context=None):
        files=episode.get('files',[])
        if not files:return '未下载'
        if any(Path(f['path']).is_file() for f in files):return '已下载'
        if any(not Path(Path(f['path']).anchor).exists() for f in files):return '磁盘离线'
        data=context if context is not None else self.read();work=next((w for w in data['works'] if w['id']==episode['work_id']),{})
        lib=next((v for v in data['libraries'] if v['id']==work.get('library_id')),None)
        if lib and not Path(lib['path']).is_dir() and any(not f.get('external') for f in files):return '存储库离线'
        return '文件缺失'

    def attach(self,episode_id,path,external=False,kind='video'):
        file=Path(path).resolve()
        if not file.is_file():raise ValueError('本地文件不存在。')
        with self.change() as data:
            ep=next(e for e in data['episodes'] if e['id']==episode_id)
            ep['files']=[f for f in ep['files'] if f['path']!=str(file)]+[{'path':str(file),'external':external,'kind':kind}];ep['status']='已下载'

    def bind(self,episode_id,task_id):
        with self.change() as data:
            ep=next(e for e in data['episodes'] if e['id']==episode_id)
            ep['task_ids']=list(dict.fromkeys([*ep.get('task_ids',[]),task_id]));ep.update(task_id=task_id,status='排队中')

    def bind_many(self,pairs):
        mapping=dict(pairs)
        with self.change() as data:
            for ep in data['episodes']:
                if ep['id'] in mapping:
                    task_id=mapping[ep['id']];ep['task_ids']=list(dict.fromkeys([*ep.get('task_ids',[]),task_id]));ep.update(task_id=task_id,status='已暂停')

    def sync(self,records):
        rows={r['id']:r for r in records};valid={}
        for row in records:
            path=(row.get('result') or {}).get('path')
            if row['state']=='completed' and path and Path(path).is_file():valid[row['id']]=str(Path(path).resolve())
        changed=False
        with self.change() as data:
            for ep in data['episodes']:
                for task_id in ep.get('task_ids',[ep.get('task_id')]):
                    path=valid.get(task_id)
                    if path and task_id not in ep.get('synced_tasks',[]):
                        if not any(f['path']==path for f in ep['files']):ep['files'].append({'path':path,'external':False,'kind':'images' if path.lower().endswith('.zip') else 'video'})
                        ep.setdefault('synced_tasks',[]).append(task_id);changed=True
                row=rows.get(ep.get('task_id'))
                if row:
                    status={'completed':'已下载','paused':'已暂停','failed':'失败','cancelled':'已取消','queued':'排队中','running':'处理中'}.get(row['state'],row['state'])
                    if ep['status']!=status:ep['status']=status;changed=True
        return changed

    def remove(self,key):
        with self.change() as data:
            data['works']=[w for w in data['works'] if w['id']!=key];data['episodes']=[e for e in data['episodes'] if e['work_id']!=key]

    def delete_owned(self,key,paths):
        ep=self.episode(key);work=self.work(ep['work_id']);data=self.read()
        lib=next((v for v in data['libraries'] if v['id']==work.get('library_id')),None)
        if not lib or not Path(lib['path']).is_dir():raise ValueError('存储库离线。')
        status=self.library_status(lib['id'])
        if not status['writable']:raise ValueError(status['error'])
        root=safe_folder(lib['path'])
        for value in paths:
            file=next((f for f in ep['files'] if f['path']==value and not f['external']),None)
            if file is None:raise ValueError('外部文件不参与此删除操作。')
            target=safe_folder(value)
            if not target.is_relative_to(root):raise ValueError('文件不属于本存储库。')
        for value in paths:Path(value).unlink()
        with self.change() as data:
            row=next(e for e in data['episodes'] if e['id']==key);row['files']=[f for f in row['files'] if f['path'] not in paths]

    def import_file(self,path,title,season='',episode='影片',work_id=None,episode_id=None):
        file=Path(path).resolve()
        if not file.is_file():raise ValueError('导入文件不存在。')
        if episode_id and episode_id!='__new__':
            ep=self.episode(episode_id)
            if ep['work_id']!=work_id:raise ValueError('选集不属于所选季。')
            self.attach(episode_id,file,True,'images' if file.suffix.lower()=='.zip' else 'video');return work_id
        old=next((e for e in self.read()['episodes'] if any(f['path']==str(file) for f in e['files'])),None)
        if old:return old['work_id']
        key='local:'+hashlib.sha256(str(file.parent).encode()).hexdigest()
        if work_id:key='local:'+work_id
        collection={'site':'本地文件','key':key+'|'+title+'|'+season,'url':'','entries':[{'key':str(file),'title':episode,'url':''}]}
        wid,eids=self.ingest(collection,title,season)
        if work_id and wid!=work_id:
            # Local imports use explicit existing work identity, without inventing a source.
            with self.change() as data:
                ep=next(e for e in data['episodes'] if e['id']==eids[0]);ep['work_id']=work_id
                data['works']=[w for w in data['works'] if w['id']!=wid];wid=work_id
        self.attach(eids[0],file,True,'images' if file.suffix.lower()=='.zip' else 'video');return wid

    def migrate(self,key,library_id,cancel=lambda:False):
        """Copy and hash all owned files first; only switch index after full success."""
        data=self.read();work=self.work(key);dest=next(v for v in data['libraries'] if v['id']==library_id)
        root=safe_folder(dest['path'])
        if not root.is_dir():raise ValueError('目标存储库离线。')
        folder=safe_folder(root/work['folder_name']);sources=[]
        if not folder.is_relative_to(root):raise ValueError('目标目录越界。')
        origin=next((v for v in data['libraries'] if v['id']==work.get('library_id')),None)
        for ep in data['episodes']:
            if ep['work_id']==key:
                for f in ep['files']:
                    if not f['external']:
                        path=safe_folder(f['path'])
                        if not origin or not path.is_relative_to(safe_folder(origin['path'])):raise ValueError('原文件不属于本季存储库，不能自动迁移。')
                        sources.append((ep['id'],f,path))
        if any(not path.is_file() for _,_,path in sources):raise ValueError('源文件缺失或离线，请先处理。')
        if shutil.disk_usage(root).free<sum(p.stat().st_size for _,_,p in sources)+100*1024**2:raise ValueError('目标磁盘空间不足。')
        folder.mkdir(parents=True,exist_ok=True);prepared=[];created=[]
        try:
            for eid,f,source in sources:
                if cancel():raise ValueError('迁移已取消，源文件保留。')
                target=folder/source.name
                if target==source:continue
                if target.exists() or target.is_symlink():raise ValueError('目标文件已存在，未覆盖：'+str(target))
                temporary=folder/('.library-copy-'+identifier());created.append(temporary);shutil.copy2(source,temporary)
                def sha(path):
                    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
                if sha(source)!=sha(temporary):raise ValueError('迁移文件校验失败，源文件保留。')
                temporary.rename(target);created.remove(temporary);created.append(target);prepared.append((eid,str(source),str(target)))
            for _,source,target in prepared:
                if sha(Path(source))!=sha(Path(target)):raise ValueError('源文件在迁移中发生变化，原文件保留。')
            with self.change() as current:
                next(w for w in current['works'] if w['id']==key)['library_id']=library_id
                for eid,source,target in prepared:
                    row=next(e for e in current['episodes'] if e['id']==eid)
                    next(f for f in row['files'] if f['path']==source)['path']=target
        except BaseException:
            for file in created:file.unlink(missing_ok=True)
            raise
        # A cleanup failure must leave the verified target and committed index usable.
        remaining=[]
        for _,source,_ in prepared:
            try:
                target=next(target for _,old,target in prepared if old==source)
                if sha(Path(source))!=sha(Path(target)):remaining.append(source);continue
                Path(source).unlink()
            except OSError:remaining.append(source)
        return remaining


def format_height(row):
    width=row.get('width') or 0;height=row.get('height') or 0
    return min(width,height) if width>0 and height>0 else height if height>0 else 0


def choose_format(info,height=1080,strict=False,kind=None):
    rows=info.get('formats',[])
    if kind in ('images','slideshow'):
        row=next((r for r in rows if r['id']==kind),None)
    else:
        candidates=[r for r in rows if format_height(r)>0 and (format_height(r)==height if strict else format_height(r)<=height)]
        row=max(candidates,key=lambda r:(format_height(r),r.get('width',0) or 0,r.get('fps',0) or 0),default=None)
        if row is None and not strict:row=next((r for r in rows if not format_height(r)),None)
    if row is None:raise ValueError('所选画质不可用，已跳过；请调整画质策略后重试。')
    return row
