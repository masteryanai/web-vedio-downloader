"""Persistent download identity; media URLs and account credentials stay in memory."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
import copy
import math
from runtime import app_data_dir


def directory():
    path = app_data_dir() / 'download-tasks'
    path.mkdir(parents=True, exist_ok=True)
    return path


def task_file(task_id):
    if not re.fullmatch('[0-9a-f]{32}', task_id):
        raise ValueError('无效下载任务编号。')
    return directory() / (task_id + '.json')


def save(task):
    target = task_file(task['id'])
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)


TERMINAL = {'completed', 'cancelled'}
_record_cache = {}


def history_file(task_id):
    name = task_file(task_id).name
    folder = directory() / 'history'
    folder.mkdir(exist_ok=True)
    return folder / name


def read_record(path, task_id):
    task = json.loads(path.read_text(encoding='utf-8'))
    fields = ('id', 'url', 'site', 'title', 'format_id', 'output', 'state')
    if not isinstance(task, dict) or any(not isinstance(task.get(key), str) for key in fields):
        raise ValueError('下载任务信息不完整。')
    if task['id'] != task_id:
        raise ValueError('下载任务信息不一致。')
    if task['state'] not in {'queued','running','paused','failed','completed','cancelled'}:
        raise ValueError('下载任务状态无效。')
    created=task.get('created_at',0)
    if type(created) not in (int,float) or not math.isfinite(created):
        task['created_at'] = 0
    percent=task.get('download_percent')
    if type(percent) not in (int,float) or not math.isfinite(percent) or not 0<=percent<=100:
        task['download_percent']=None
    if not isinstance(task.get('stage',''),str):task['stage']=''
    return task


def load(task_id):
    return read_record(task_file(task_id), task_id)


def load_record(task_id):
    return load(task_id) if task_file(task_id).exists() else read_record(history_file(task_id),task_id)


def records():
    rows = {}
    seen = set()
    # A terminal checkpoint in the active directory wins over an older archive.
    for folder in (directory()/'history', directory()):
        for file in folder.glob('*.json'):
            try:
                task_file(file.stem)
                stat = file.stat(); fingerprint = (stat.st_mtime_ns,stat.st_size)
                seen.add(file)
                cached = _record_cache.get(file)
                if cached and cached[0] == fingerprint:
                    task = copy.deepcopy(cached[1])
                else:
                    task = read_record(file,file.stem)
                    _record_cache[file] = (fingerprint,copy.deepcopy(task))
                if folder.name == 'history' and task['state'] not in TERMINAL:
                    continue
                if task['state'] not in TERMINAL:
                    work_path(task)
                rows[task['id']] = task
            except (OSError, ValueError, KeyError, TypeError):
                continue
    for file in list(_record_cache):
        if file not in seen: _record_cache.pop(file,None)
    return list(rows.values())


def pending():
    return [task for task in records() if task['state'] not in TERMINAL]


def archive(task):
    if task['state'] not in TERMINAL:
        raise ValueError('只能归档已完成或已取消的任务。')
    task.setdefault('finished_at',time.time())
    if task['state']=='completed':
        task.update(stage='已完成',download_percent=100)
    fields = ('id','url','site','title','format_id','format','output','state','stage',
              'created_at','finished_at','download_percent','result')
    public = {key:task[key] for key in fields if key in task}
    target = history_file(task['id']); temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(public,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(target)


def finish(task):
    archive(task)
    remove(task)


def delete_history(task_id, clean_cache=False):
    with lock(task_id):
        task = load_record(task_id)
        if task['state'] not in TERMINAL:
            raise ValueError('未完成任务请先取消，不能直接删除历史记录。')
        if clean_cache:
            cache=work_path(task)
            result=task.get('result')
            published=result.get('path') if isinstance(result,dict) else None
            if published and Path(published).resolve().is_relative_to(cache.resolve()):
                raise ValueError('成品位于缓存目录内，已停止清理；请选择仅删除记录。')
            if cache.exists():shutil.rmtree(cache)
        history_file(task_id).unlink(missing_ok=True)
        task_file(task_id).unlink(missing_ok=True)


def create(info, format_id, output):
    selected = next((row for row in info.get('formats', []) if row['id'] == format_id), {})
    task = {'id':uuid.uuid4().hex, 'url':info['url'],
            'site':info['site'], 'title':info.get('title', '视频'), 'format_id':format_id,
            'format':{key:selected[key] for key in ('width','height','fps','vcodec','acodec') if selected.get(key) is not None},
            'output':str(Path(output).expanduser().resolve()), 'state':'paused', 'created_at':time.time()}
    save(task)
    return task


def work_path(task):
    task_file(task['id'])
    output = Path(task['output']).resolve()
    path = output / ('.video-downloader-' + task['id'])
    if path.is_symlink() or path.resolve().parent != output:
        raise ValueError('任务缓存路径不安全。')
    return path


@contextmanager
def lock(task_id):
    path = task_file(task_id).with_suffix('.lock')
    with path.open('a+b') as handle:
        if not path.stat().st_size:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('该任务正在另一个窗口下载，请先暂停。') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def remove(task):
    """Caller holds the task lock; delete only the task's verified cache root."""
    path = work_path(task)
    if path.exists():
        shutil.rmtree(path)
    task_file(task['id']).unlink(missing_ok=True)


def discard(task_id):
    with lock(task_id):
        task = load(task_id)
        if task['state'] == 'completed':
            finish(task)
            return task
        # Commit cancellation only after the worker has stopped and cache removal succeeds.
        path = work_path(task)
        if path.exists(): shutil.rmtree(path)
        task.update(state='cancelled',stage='已取消')
        task.pop('result',None)
        save(task)
        archive(task)
        task_file(task_id).unlink(missing_ok=True)
        return task
