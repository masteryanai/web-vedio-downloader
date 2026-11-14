"""GUI-thread scheduler; each worker owns its task through prepare, download and verify."""
from collections import deque
import copy
import threading
import time
from PySide6.QtCore import QObject, QThread, Signal, Slot, QTimer
import download_tasks as tasks
import engine
import diagnostics


class Job(QObject):
    resolved = Signal(str, object)
    progress = Signal(str, object)
    message = Signal(str, str)
    result = Signal(str, object, str)
    finished = Signal()

    def __init__(self, task_id, payload, cookies, cancel):
        super().__init__()
        self.task_id, self.payload, self.cookies, self.cancel = task_id, payload, cookies, cancel

    @Slot()
    def run(self):
        task_id = self.task_id
        diagnostics.event('queue.worker.begin',task_id)
        try:
            with tasks.lock(task_id):
                task = tasks.load(task_id)
                if task['state'] in tasks.TERMINAL:
                    raise ValueError('历史任务不能直接继续。')
                task['state'] = 'running'; task['stage'] = '准备媒体地址'; tasks.save(task)
                try:
                    engine._check_cancel(self.cancel)
                    def log(text):
                        self.message.emit(task_id, text)
                        self.progress.emit(task_id, {'stage':text})
                    last_report = [0.0, None]
                    def progress(data):
                        now = time.monotonic()
                        if (now-last_report[0] >= .1 or data.get('download_percent') == 100
                                or data.get('download_percent') is None and data.get('stage') != last_report[1]):
                            self.progress.emit(task_id, data)
                            last_report[:] = [now, data.get('stage')]
                    info = self.payload
                    if info is None:
                        self.progress.emit(task_id, {'stage':'重新解析媒体地址', 'download_percent':task.get('download_percent')})
                        info = engine.inspect_video(task['url'], self.cookies, log, self.cancel)
                    self.resolved.emit(task_id, info)
                    result = engine.download_video(info, task['format_id'], task['output'], self.cookies,
                        progress, log,
                        self.cancel, task_id=task_id, lock_held=True)
                    self.result.emit(task_id, result, '')
                except Exception as error:
                    diagnostics.exception('queue.worker.failed',error,error.__traceback__)
                    if tasks.task_file(task_id).exists():
                        latest = tasks.load(task_id)
                        if latest['state'] not in tasks.TERMINAL:
                            latest['state'] = 'paused' if self.cancel.is_set() else 'failed'
                            tasks.save(latest)
                    raise
        except Exception as error:
            self.result.emit(task_id, None, str(error))
        finally:
            self.finished.emit()


class DownloadQueue(QObject):
    changed = Signal()
    progress = Signal(str, object)
    message = Signal(str, str)
    completed = Signal(str, object)
    failed = Signal(str, str)
    removed = Signal(str)
    idle = Signal()

    def __init__(self, limit=5, parent=None):
        super().__init__(parent)
        self.limit = limit
        self.waiting = deque()
        self.active = {}
        self.payloads = {}
        self.snapshots = {}
        self.entries = {}
        self.completed_rows = {}
        self.deleting = set()
        self.stopping = False

    def cached(self, task_id):
        entry = self.payloads.get(task_id)
        return copy.deepcopy(entry[0]) if entry and time.monotonic()-entry[1] < 300 else None

    def enqueue(self, task_id, cookies, info=None):
        diagnostics.event('queue.enqueue',task_id)
        if self.stopping or task_id in self.active or any(row[0] == task_id for row in self.waiting):
            return False
        with tasks.lock(task_id):
            task = tasks.load(task_id)
            if task['state'] in tasks.TERMINAL: return False
            task['state'] = 'queued'; tasks.save(task)
        if info is not None:
            self.payloads[task_id] = (copy.deepcopy(info), time.monotonic())
        self.waiting.append((task_id, copy.deepcopy(cookies)))
        self.changed.emit(); QTimer.singleShot(0, self.pump)
        return True

    def set_limit(self, limit):
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError('并行下载数须为 1～10。')
        self.limit = limit; self.pump(); self.changed.emit()

    @Slot()
    def pump(self):
        while not self.stopping and self.waiting and len(self.active) < self.limit:
            task_id, cookies = self.waiting.popleft()
            thread = QThread(self); thread.setProperty('task_id', task_id)
            try: self.entries[task_id] = tasks.load(task_id)
            except (OSError, ValueError): continue
            cancel = threading.Event()
            job = Job(task_id, self.cached(task_id), cookies, cancel); job.moveToThread(thread)
            self.active[task_id] = (thread, job, cancel)
            thread.started.connect(job.run)
            job.progress.connect(self.on_progress); job.message.connect(self.message)
            job.resolved.connect(self.on_resolved)
            job.result.connect(self.on_result); job.finished.connect(thread.quit)
            job.finished.connect(job.deleteLater)
            thread.finished.connect(self.on_thread_finished); thread.finished.connect(thread.deleteLater)
            thread.start()
        self.changed.emit()
        if not self.active and not self.waiting:
            self.idle.emit()

    @Slot(str, object)
    def on_resolved(self, task_id, info):
        self.payloads[task_id] = (copy.deepcopy(info), time.monotonic())

    @Slot(str, object)
    def on_progress(self, task_id, data):
        snapshot = self.snapshots.setdefault(task_id, {})
        snapshot.update({k:v for k,v in data.items() if k != 'download_percent' or v is not None or data.get('phase')=='download'})
        self.progress.emit(task_id, data)

    @Slot(str, object, str)
    def on_result(self, task_id, result, error):
        diagnostics.event('queue.result',task_id+(': failed '+error if error else ': completed'))
        if result:
            # Keep completed rows for this session; completed files remain outside task caches.
            row = self.entries.get(task_id)
            if row:
                row.update(state='completed', stage='已完成', download_percent=100, result=result)
                self.completed_rows[task_id] = row
            self.payloads.pop(task_id, None)
            self.completed.emit(task_id,result)
        elif error:
            try:
                task = tasks.load(task_id)
                self.snapshots.setdefault(task_id, {}).update({key:task.get(key) for key in ('download_percent','stage')})
            except (OSError, ValueError):
                pass
            if '过期' in error or '验证' in error:
                self.payloads.pop(task_id, None)
            self.failed.emit(task_id,error)

    @Slot()
    def on_thread_finished(self):
        task_id = self.sender().property('task_id')
        self.active.pop(task_id, None)
        if task_id in self.deleting:
            self.deleting.discard(task_id)
            self.discard(task_id)
        self.pump()

    def pause(self, task_id):
        diagnostics.event('queue.pause',task_id)
        if task_id in self.active:
            self.active[task_id][2].set()
        else:
            self.waiting = deque(row for row in self.waiting if row[0] != task_id)
            with tasks.lock(task_id):
                task = tasks.load(task_id)
                if task['state'] in tasks.TERMINAL: return False
                task['state'] = 'paused'; tasks.save(task)
            self.pump()
        self.changed.emit()
        return True

    def discard(self, task_id):
        diagnostics.event('queue.cancel',task_id)
        if task_id in self.active:
            self.deleting.add(task_id); self.pause(task_id); return True
        self.waiting = deque(row for row in self.waiting if row[0] != task_id)
        try:
            record=tasks.load_record(task_id)
            if record['state'] in tasks.TERMINAL:
                self.message.emit(task_id,'任务已经结束，文件和历史记录已保留。')
                return False
            else:
                tasks.discard(task_id)
                self.snapshots.pop(task_id,None);self.payloads.pop(task_id,None);self.entries.pop(task_id,None)
                self.removed.emit(task_id)
        except Exception as error:
            self.failed.emit(task_id, '缓存删除失败：'+str(error))
            return False
        self.pump()
        return True

    def delete_record(self,task_id,clean_cache=False):
        if task_id in self.active or any(row[0]==task_id for row in self.waiting):
            raise ValueError('任务仍在运行或排队，不能删除记录。')
        tasks.delete_history(task_id,clean_cache=clean_cache)
        self.completed_rows.pop(task_id,None);self.snapshots.pop(task_id,None)
        self.entries.pop(task_id,None);self.payloads.pop(task_id,None)
        self.changed.emit()

    def rows(self):
        result = {row['id']:row for row in tasks.records()}
        for task_id, row in result.items():
            if row['state'] not in tasks.TERMINAL:
                row.update(self.snapshots.get(task_id, {}))
        return sorted(result.values(), key=lambda row:(row.get('created_at',0),row['id']))

    def shutdown(self):
        self.stopping = True
        for task_id, _ in list(self.waiting): self.pause(task_id)
        for _, _, cancel in self.active.values(): cancel.set()
        if not self.active: self.idle.emit()
