"""User-confirmed library recovery and whole-library migration workflows."""
from pathlib import Path
import os
import time

from PySide6.QtCore import QObject, Signal, Slot, Qt, QTimer
from PySide6.QtWidgets import (QFileDialog, QMessageBox, QInputDialog, QProgressDialog)


def verify_sources(data, library_id, cookies, cancel, progress=lambda value: None,
                   discover=None, inspect=None):
    """Validate stable source identities; never download or overwrite local media."""
    from collection_media import discover as find_collection, single
    from engine import inspect_video
    from media_library import source_key
    discover = discover or find_collection
    inspect = inspect or inspect_video
    works = [w for w in data['works'] if w.get('library_id') == library_id]
    checked, failures = [], []
    for number, work in enumerate(works):
        if cancel.is_set():
            raise RuntimeError('已取消来源核对，当前只读资料和视频保留。')
        sources = work.get('sources', [])
        source = next((s for s in sources if s['key'] == work.get('default_source')),
                      sources[0] if sources else None)
        progress({'phase': 'sources', 'files_done': number, 'files_total': len(works),
                  'path': work['title']})
        if not source or not source.get('url'):
            failures.append(work['title'] + '：没有可回溯的网页来源')
            continue
        try:
            session = cookies.get(source['site'], [])
            group = discover(source['url'], session, cancel)
            if group is None:
                group = single(inspect(source['url'], session, cancel=cancel))
            if source_key(group['site'], group['key'], group.get('variant', '')) != source['key']:
                raise ValueError('网页返回的作品或线路身份不一致')
            available = {str(entry['key']) for entry in group['entries']}
            wanted = {str(s['key']) for ep in data['episodes'] if ep['work_id'] == work['id']
                      for s in ep.get('sources', []) if s['source'] == source['key']}
            if not wanted <= available:
                raise ValueError(f'{len(wanted - available)} 个原选集无法核对')
            checked.append((work['id'], group))
        except Exception as error:
            failures.append(work['title'] + '：' + str(error)[:240])
    return {'checked': checked, 'failures': failures, 'total': len(works)}


class LibraryManager(QObject):
    progress = Signal(object)

    def __init__(self, workspace, dialog):
        super().__init__(dialog)
        self.ws = workspace
        self.dialog = dialog
        self.closed = False
        self.busy = False
        self.cancel = None
        self.progress_dialog = None
        self.progress.connect(self.show_progress, Qt.ConnectionType.QueuedConnection)
        dialog.finished.connect(self.closed_dialog)

    @Slot(int)
    def closed_dialog(self, _):
        self.closed = True
        if self.cancel:
            self.cancel.set()

    def notify(self, title, text):
        if not self.closed:
            QMessageBox.information(self.dialog, title, text)

    def failed(self, error):
        self.finish_progress()
        self.busy = False
        if not self.closed:
            QMessageBox.warning(self.dialog, '操作未完成', str(error)[:1500])
            self.dialog.reload()

    def run(self, action, done, title='正在读取存储库资料…'):
        if self.busy or self.closed:
            return
        self.busy = True
        self.progress_dialog = QProgressDialog(title, '取消', 0, 0, self.dialog)
        self.progress_dialog.setWindowTitle('存储库')
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.show()

        def completed(value):
            self.finish_progress()
            self.busy = False
            if not self.closed:
                done(value)

        self.cancel = self.ws.jobs.start(action, completed, self.failed)
        self.progress_dialog.canceled.connect(lambda: self.cancel.set() if self.cancel else None)

    def finish_progress(self):
        if self.progress_dialog:
            self.progress_dialog.reset()
            self.progress_dialog.deleteLater()
            self.progress_dialog = None
        self.cancel = None

    @Slot(object)
    def show_progress(self, value):
        if self.closed or not self.progress_dialog:
            return
        labels = {'scan': '检查文件', 'copy': '复制文件', 'verify': '校验文件',
                  'mount': '更新库与任务位置', 'cleanup': '清理已核对的原件', 'sources': '核对网页来源'}
        phase = value.get('phase', '')
        if phase in ('mount', 'cleanup'):
            self.progress_dialog.setCancelButton(None)
        done, total = value.get('files_done', value.get('files', 0)), value.get('files_total', value.get('total_files', 0))
        path = str(value.get('path') or value.get('file') or '')
        self.progress_dialog.setLabelText(labels.get(phase, '处理存储库') +
                                         (f' · {done} / {total}' if total else '') + '\n' + path[-160:])
        if total:
            self.progress_dialog.setRange(0, 1000)
            self.progress_dialog.setValue(min(999, int(done * 1000 / total)))

    def refresh(self):
        self.ws.index_error = ''
        self.dialog.reload()
        self.ws.add_controls.reload_libraries()
        self.ws.refresh(True)
        for row in __import__('download_tasks').pending():
            if row['id'] not in self.ws.active_ids():
                self.ws.window.queue.initial_pending[row['id']] = row
        self.ws.window.queue.changed.emit()

    def add(self, path=None):
        if self.busy:
            return
        path = path or QFileDialog.getExistingDirectory(self.dialog, '选择新库或已有库目录',
                         options=QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return
        self.run(lambda cancel: self.ws.store.probe_library(path),
                 lambda preview: self.offer(path, preview, 'current'))

    def offer(self, path, preview, recovery):
        state = preview['state']
        if state == 'empty':
            if QMessageBox.question(self.dialog, '新建存储库',
                    '此目录没有库恢复资料。新建后会保存今后的作品与来源记录，原有文件不会被移动或删除。是否创建？') != QMessageBox.StandardButton.Yes:
                return
            self.run(lambda cancel: self.ws.store.library(path), lambda _: self.refresh())
            return
        if state != 'valid':
            if state == 'unknown':
                self.notify('资料格式暂不支持', preview.get('error', '') + '\n原件保留，请使用兼容版本。')
                return
            choices = [('历史记录 ' + str(h.get('id', h)), 'history:' + str(h.get('id', h)))
                       for h in preview.get('histories', [])]
            if preview.get('sources_available'):
                choices.append(('来源清单（需联网核对）', 'sources'))
            if not choices:
                self.notify('未找到有效恢复资料', preview.get('error', '') + '\n视频保留，可使用普通导入。')
                return
            selected, ok = QInputDialog.getItem(self.dialog, '选择恢复依据',
                '当前资料无法读取，损坏原件会保留。', [v[0] for v in choices], 0, False)
            if ok:
                recovery = dict(choices)[selected]
                self.run(lambda cancel: self.ws.store.probe_library(path, recovery),
                         lambda value: self.offer(path, value, recovery))
            return
        mode = 'restore'
        existing = next((v for v in self.ws.store.read()['libraries']
                         if os.path.normcase(str(Path(v['path']).resolve())) == os.path.normcase(str(Path(path).resolve()))), None)
        if existing and recovery == 'current':
            self.notify('存储库已挂载', '此目录已经登记。需要补存恢复资料时，请使用“重试保存 / 来源恢复”。')
            return
        if preview.get('conflict'):
            choice = QMessageBox(self.dialog)
            choice.setWindowTitle('识别到相同的库身份')
            choice.setText('原登记位置：' + preview['conflict']['path'] + '\n新位置：' + str(path))
            moved = choice.addButton('原库搬家', QMessageBox.ButtonRole.AcceptRole)
            cloned = choice.addButton('独立副本', QMessageBox.ButtonRole.ActionRole)
            choice.addButton('取消', QMessageBox.ButtonRole.RejectRole)
            choice.exec()
            if choice.clickedButton() == moved:
                mode = 'relocate'
            elif choice.clickedButton() == cloned:
                mode = 'clone'
            else:
                return
        text = (f"识别到 {preview['works']} 个作品、{preview['episodes']} 个选集。\n"
                f"不可用文件：{len(preview.get('missing_files', []))} 个。\n"
                '确认后恢复库记录，不重新下载、移动或改名。')
        if recovery == 'sources':
            text += '\n将先以只读方式载入，再联网核对来源；不会直接认定恢复成功。'
        if QMessageBox.question(self.dialog, '确认添加存储库', text) != QMessageBox.StandardButton.Yes:
            return
        self.run(lambda cancel: self.ws.store.mount_library(path, mode, recovery),
                 lambda key: self.mounted(key, recovery))

    def mounted(self, key, recovery):
        self.refresh()
        if recovery == 'sources':
            self.validate_sources(key)
        else:
            state = self.ws.store.library_status(key)
            self.notify('存储库已添加', '作品与选集已载入。' +
                        ('\n当前只读：' + state['error'] if not state['writable'] else '\n未完成任务处于待继续状态，不会自动下载。'))

    def validate_sources(self, key):
        data = self.ws.store.read()
        cookies = {site: self.ws.window.accounts.cookies_for(site) for site in __import__('runtime').SITES}
        self.run(lambda cancel: verify_sources(data, key, cookies, cancel, self.progress.emit),
                 lambda result: self.source_result(key, result), '正在核对网页来源…')

    def source_result(self, key, result):
        if result['failures']:
            self.notify('来源未全部核对成功', f"匹配 {len(result['checked'])} / {result['total']} 个作品。\n" +
                        '\n'.join(result['failures'][:8]) + '\n当前以只读方式保留，视频未改变。可稍后重试。')
            return
        if QMessageBox.question(self.dialog, '确认来源恢复',
                f"已核对 {len(result['checked'])} 个作品的来源和原选集身份。\n"
                '只恢复能核对的关联与资料，不会自动下载。网页无法补回的自定义设置使用默认值。是否确认？') != QMessageBox.StandardButton.Yes:
            return

        def apply(cancel):
            self.ws.store.confirm_source_restore(key)
            for wid, group in result['checked']:
                if cancel.is_set():
                    break
                work = self.ws.store.work(wid)
                self.ws.store.ingest(group, work['title'], work.get('season', ''), key)
                with self.ws.store.change() as data:
                    value = next(w for w in data['works'] if w['id'] == wid)
                    value.update(cover_url=group.get('cover', ''), banner_url=group.get('banner', ''))
            return key
        self.run(apply, lambda _: (self.refresh(), self.notify('来源恢复完成', '库记录已保存，媒体文件未改动。')))

    def selected(self):
        return self.dialog.disks.currentData()

    def retry(self):
        key = self.selected()
        if not key:
            return
        if self.ws.store.library_status(key)['state'] == 'needs_online_confirmation':
            self.validate_sources(key)
        else:
            state = self.ws.store.library_status(key)
            if state['state'] == 'corrupt' and state.get('pending'):
                if QMessageBox.question(self.dialog, '重建恢复资料',
                        '库内资料已损坏。将保留损坏原件，并使用程序当前记录重新保存；视频不改变。是否继续？') != QMessageBox.StandardButton.Yes:
                    return
            self.run(lambda cancel: self.ws.store.retry_persist(key),
                     lambda _: (self.refresh(), self.notify('恢复资料已保存', '已更新库内恢复资料。')))

    def repair_index(self):
        if self.ws.window.is_busy() or self.ws.window.queue.active or self.ws.window.queue.waiting:
            self.notify('请先停止任务', '索引修复前请暂停并等待全部任务停止。')
            return
        try:
            self.ws.store.read()
        except (ValueError, OSError):
            pass
        else:
            self.notify('索引当前可读', '无需重建。可直接添加或恢复已有存储库。')
            return
        if QMessageBox.question(self.dialog, '重建程序索引',
                '原索引将原样保留，再创建空的程序目录索引。影片和库内恢复资料不删除；随后可重新添加各个库。是否继续？') != QMessageBox.StandardButton.Yes:
            return
        self.run(lambda cancel: self.ws.store.rebuild_index(),
                 lambda _: (self.refresh(), self.notify('程序索引已重建', '旧原件已保留。现在可添加已有存储库。')))

    def move(self):
        key = self.selected()
        if not key or self.busy:
            return
        state = self.ws.store.library_status(key)
        if not state['writable']:
            self.notify('当前不能迁移', state['error'])
            return
        if self.ws.batch_preparing or self.ws.batch_worker:
            self.notify('批量任务仍在准备', '请等待准备结束或取消准备后再迁移。')
            return
        target = QFileDialog.getExistingDirectory(self.dialog, '选择空的目标库目录',
                         options=QFileDialog.Option.DontUseNativeDialog)
        if not target:
            return
        if QMessageBox.question(self.dialog, '迁移整个存储库',
                '将暂停此库的任务，复制并校验库内全部文件（包括字幕和附带文件），确认成功后才清理未变化的原件。'
                '\n库外引用文件保持原位。目标：' + target + '\n是否继续？') != QMessageBox.StandardButton.Yes:
            return
        root = next(v['path'] for v in self.ws.store.read()['libraries'] if v['id'] == key)
        relevant = {row['id'] for row in __import__('download_tasks').records()
                    if Path(row['output']).is_relative_to(Path(root))}
        for task_id in relevant & self.ws.active_ids():
            self.ws.window.queue.pause(task_id)
        started = time.monotonic()

        def wait_stopped():
            if self.closed:
                return
            if relevant & self.ws.active_ids():
                if time.monotonic() - started > 60:
                    self.failed('任务尚未停止，未迁移文件。请稍后重试。')
                else:
                    QTimer.singleShot(100, wait_stopped)
                return
            from library_transfer import move_library
            self.run(lambda cancel: move_library(self.ws.store, key, target, cancel.is_set, self.progress.emit),
                     self.moved, '正在准备迁移…')
        wait_stopped()

    def moved(self, result):
        self.refresh()
        if result['success']:
            note = '目标文件已校验，库与待继续任务的位置已更新。'
            if result.get('retained_source'):
                note += '\n原目录仍有未清理内容，已保留：' + result['source']
            self.notify('整库迁移完成', note)
        else:
            self.notify('迁移未完成', str(result.get('error', '')) +
                        '\n原件保留：' + result['source'] + '\n目标保留位置：' + result['destination'])
