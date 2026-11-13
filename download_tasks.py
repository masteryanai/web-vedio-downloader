"""Persistent download identity; media URLs and account credentials stay in memory."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import uuid
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


def load(task_id):
    task = json.loads(task_file(task_id).read_text(encoding='utf-8'))
    fields = ('id', 'url', 'site', 'title', 'format_id', 'output', 'state')
    if not isinstance(task, dict) or any(not isinstance(task.get(key), str) for key in fields):
        raise ValueError('下载任务信息不完整。')
    if task['id'] != task_id:
        raise ValueError('下载任务信息不一致。')
    return task


def pending():
    rows = []
    for file in directory().glob('*.json'):
        try:
            task = load(file.stem)
            work_path(task)
            rows.append(task)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return rows


def create(info, format_id, output):
    selected = next((row for row in info.get('formats', []) if row['id'] == format_id), {})
    task = {'id':uuid.uuid4().hex, 'url':info['url'],
            'site':info['site'], 'title':info.get('title', '视频'), 'format_id':format_id,
            'format':{key:selected[key] for key in ('width','height','fps','vcodec','acodec') if selected.get(key) is not None},
            'output':str(Path(output).expanduser().resolve()), 'state':'paused'}
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
        remove(load(task_id))
