"""Update center: rendered notes, explicit install and compatible history rollback."""
import threading
from PySide6.QtCore import QObject,QThread,QTimer,Signal,Slot,Qt,QUrl
from PySide6.QtGui import QTextDocument,QDesktopServices
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTextBrowser,QPushButton,
    QProgressBar,QComboBox,QMessageBox,QApplication,QFrame)
from runtime import APP_VERSION
import diagnostics


def can_install(parent):
    if parent is None:return True
    queue=getattr(parent,'queue',None);dialog=getattr(parent,'diagnostic_dialog',None)
    return not ((getattr(parent,'is_busy',lambda:False)()) or (queue and (queue.active or queue.waiting)) or getattr(parent,'resume_pending',[]) or (dialog and dialog.thread is not None))


class Notes(QTextBrowser):
    def __init__(self):
        super().__init__();self.setOpenLinks(False);self.setOpenExternalLinks(False);self.anchorClicked.connect(self.link)
    def link(self,url):
        if url.scheme() in ('https','http'):QDesktopServices.openUrl(url)
    def loadResource(self,kind,url):return None
    def render(self,text):self.document().setMarkdown(text,QTextDocument.MarkdownFeature.MarkdownDialectGitHub|QTextDocument.MarkdownFeature.MarkdownNoHTML)


class Job(QObject):
    success=Signal(object);failure=Signal(object);progress=Signal(object);finished=Signal()
    def __init__(self,action):super().__init__();self.action=action
    @Slot()
    def run(self):
        try:self.success.emit(self.action(self.progress.emit))
        except Exception as error:diagnostics.exception('update.center.failed',error,error.__traceback__);self.failure.emit(error)
        finally:self.finished.emit()


class UpdateCenter(QDialog):
    def __init__(self,parent=None,scheduler=None):
        super().__init__(parent);self.scheduler=scheduler;self.setWindowTitle('更新中心');self.resize(760,760)
        self.thread=None;self.job=None;self.info=None;self.prepared=None;self.cancel=threading.Event();self.closing=False;self.selection=None;self.need_confirm=False;self.catalog=[];self.catalog_manifest=None;self.pending_info=None;self.latest_info=None;self.awaiting_offer=False;self.launching=False;self.restored=False
        box=QVBoxLayout(self)
        self.version_label=QLabel(f'当前版本 {APP_VERSION}');self.version_label.setWordWrap(True);box.addWidget(self.version_label)
        self.progress_card=QFrame();self.progress_card.setStyleSheet('QFrame {background:#eef4ff;border-radius:8px;}');status=QVBoxLayout(self.progress_card)
        self.steps=QLabel('检查本地 → 下载 → 准备 → 校验 → 待安装 → 更新重启');status.addWidget(self.steps)
        self.label=QLabel('正在检查更新…');self.label.setWordWrap(True);self.label.setStyleSheet('font-size:18px;font-weight:600;');status.addWidget(self.label)
        self.metrics=QLabel();self.metrics.setStyleSheet('font-size:22px;font-weight:600;color:#285ed4;');self.metrics.setWordWrap(True);status.addWidget(self.metrics)
        self.progress_detail=QLabel();self.progress_detail.setWordWrap(True);status.addWidget(self.progress_detail)
        self.bar=QProgressBar();self.bar.setRange(0,100);self.bar.setValue(0);self.bar.setMinimumHeight(22);status.addWidget(self.bar);box.addWidget(self.progress_card)
        row=QHBoxLayout();self.versions=QComboBox();self.versions.addItem('最新版本',None);self.versions.currentIndexChanged.connect(self.choose);row.addWidget(self.versions)
        self.history_button=QPushButton('历史版本 / 回退');self.history_button.clicked.connect(self.load_history);row.addWidget(self.history_button);box.addLayout(row)
        self.restore=QPushButton('恢复本地备份（无需网络）');self.restore.clicked.connect(self.restore_local);box.addWidget(self.restore)
        self.detail=QLabel('仅检查和提示；由你确认后才下载安装。');self.detail.setWordWrap(True);box.addWidget(self.detail)
        self.notes=Notes();box.addWidget(self.notes,1)
        self.install=QPushButton('下载更新');self.install.setEnabled(False);self.install.clicked.connect(self.download);box.addWidget(self.install)
        row=QHBoxLayout();self.later=QPushButton('稍后提醒（4 小时）');self.ignore=QPushButton('忽略此版本');row.addWidget(self.later);row.addWidget(self.ignore);box.addLayout(row)
        self.later.clicked.connect(self.snooze);self.ignore.clicked.connect(self.ignore_version)
        page=QPushButton('打开发布页');page.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('https://github.com/masteryanai/web-vedio-downloader/releases')));box.addWidget(page)
        self.stop=QPushButton('关闭');self.stop.clicked.connect(self.close);box.addWidget(self.stop)
        self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.refresh);self.timer.start()
    def busy(self):return self.thread is not None
    def start(self,action,success):
        if self.busy():return
        self.install.setEnabled(False);self.thread=QThread(self);self.job=Job(action);self.job.moveToThread(self.thread)
        self.thread.started.connect(self.job.run);self.job.success.connect(success);self.job.failure.connect(self.failed);self.job.progress.connect(self.transfer_progress)
        self.job.finished.connect(self.thread.quit);self.job.finished.connect(self.job.deleteLater);self.thread.finished.connect(self.done_job);self.thread.finished.connect(self.thread.deleteLater);self.thread.start()
    def receive(self,info):
        if self.prepared:return
        if self.busy():self.pending_info=info;return
        self.closing=False;self.cancel.clear();self.info=info;self.latest_info=info;self.selection=None;self.need_confirm=False
        self.version_label.setText(f'当前 {APP_VERSION} → 目标 {info["version"]} · 发布：{info.get("manifest",{}).get("published_at","未知")}' if info.get('available') else info.get('message','未发现新版本'))
        self.notes.render(info.get('notes',''));self.label.setText('发现新版本，可下载更新' if info.get('available') else info.get('message','未发现更新'))
        if info.get('available') and info.get('manifest',{}).get('schema')==2:
            self.catalog_manifest=info['manifest'];self.preview()
        self.refresh()
    def preview(self):
        from update_package import preview
        try:self.previewed(preview(self.info))
        except (ValueError,KeyError,TypeError) as error:self.failed(error);return
        self.label.setText('可下载更新 · 尚未安装')
        self.metrics.setText(f"{self.selection['size']/1048576:.1f} MiB")
        self.progress_detail.setText('点击下载时检查本地文件；完整准备后由你确认重启。');self.refresh()
    def previewed(self,result):
        self.selection,self.need_confirm=result
        title={'delta':'预计增量更新','full':'全量更新','local':'本地备份恢复'}[self.selection['mode']]
        self.detail.setText(f'{title} · {self.selection["size"]/1048576:.1f} MiB · '+self.selection['reason'])
        self.version_label.setText(f'当前 {APP_VERSION} → 目标 {self.info["version"]} · 发布：{self.info.get("manifest",{}).get("published_at","未知")}')
    def refresh(self):
        ready=bool(self.info and self.info.get('available')) and (self.info.get('manifest',{}).get('schema')!=2 or self.selection is not None)
        if self.prepared:self.install.setText('更新并重启应用')
        elif self.info and self.info.get('operation')=='rollback':self.install.setText('准备本地备份回退' if self.selection and self.selection.get('mode')=='local' else '下载完整包并回退')
        else:self.install.setText('下载更新')
        self.install.setEnabled(ready and not self.launching and not self.busy() and can_install(self.parentWidget()))
        self.install.setToolTip('请等待解析、下载或诊断导出结束后安装' if not can_install(self.parentWidget()) else '')
        self.restore.setEnabled(not self.busy() and not self.prepared)
        self.history_button.setEnabled(not self.busy() and not self.prepared);self.versions.setEnabled(not self.busy() and not self.prepared)
        self.ignore.setEnabled(ready and not self.prepared and not self.busy() and self.info.get('operation')!='rollback');self.later.setEnabled(not self.busy());self.later.setText('稍后安装' if self.prepared else '稍后提醒（4 小时）')
    def load_history(self):
        def action(progress):
            import updater
            from update_package import history
            manifest=self.catalog_manifest
            if not manifest:
                import re
                with updater.request(updater.LATEST) as response:url=response.geturl()
                match=re.fullmatch(re.escape(updater.TAGS)+r'(v?\d+\.\d+\.\d+)',url)
                if not match:raise ValueError('发布版本重定向无效。')
                manifest=updater.get_json(updater.PREFIX+match[1]+'/update.json',True);updater.validate_manifest(manifest,match[1])
            return manifest,history(manifest)
        self.start(action,self.history_ready)
    def history_ready(self,result):
        from update_protocol import version
        self.catalog_manifest,self.catalog=result;self.versions.blockSignals(True);self.versions.clear();self.versions.addItem('最新版本 v'+self.catalog_manifest['version'],None)
        for entry in self.catalog:
            if version(entry['version'])>=version(APP_VERSION):continue
            self.versions.addItem('v'+entry['version']+('（不可回退）' if not entry['rollback_allowed'] else ''),entry)
            if not entry['rollback_allowed']:self.versions.model().item(self.versions.count()-1).setEnabled(False)
        self.versions.blockSignals(False)
        self.detail.setText('选择历史版本查看说明；优先使用完整且兼容的本地备份，否则下载完整包。')
    def restore_local(self):
        from pathlib import Path
        import sys
        from update_package import backup_info
        backup=backup_info(Path(sys.executable).resolve().parent)
        if not backup:self.label.setText('没有可用的受管理本地备份。请使用历史版本完整包。');return
        self.info={'available':True,'version':backup['version'],'operation':'rollback','rollback_allowed':True,'local_restore':True,'manifest':{'schema':2}}
        self.cancel.clear();self.selection=None;self.preview()
    def choose(self,index):
        entry=self.versions.itemData(index)
        if not entry:
            if self.latest_info:self.receive(self.latest_info)
            return
        self.info={'available':True,'version':entry['version'],'operation':'rollback','rollback_allowed':entry['rollback_allowed'],
            'notes':entry.get('notes',''),'manifest':{**entry['package'],'published_at':entry.get('published_at','未知'),'schema':2,'inventory':entry['inventory'],'data_schema':entry['data_schema']}}
        self.selection=None;self.prepared=None;self.cancel.clear();self.notes.render(self.info['notes']);self.preview()
    def transfer_progress(self,value):
        if isinstance(value,int):self.bar.setValue(value);return
        phase=value.get('phase','download' if 'total' in value else 'verify')
        self.label.setText(value.get('stage','正在准备更新'))
        self.steps.setText({'baseline':'当前阶段：检查本地文件','download':'当前阶段：下载更新包','copy':'当前阶段：下载完成 · 准备安装文件',
            'archive_verify':'当前阶段：下载完成 · 校验下载包','extract':'当前阶段：下载完成 · 解压更新文件','verify':'当前阶段：下载完成 · 校验完整目标'}.get(phase,'正在准备更新'))
        if 'total' in value and phase=='download':
            done=value['bytes'];total=value['total'];speed=value.get('speed',0)
            self.bar.setValue(min(100,int(done*100/max(1,total))))
            self.metrics.setText(f'{speed/1048576:.2f} MiB/s')
            eta=f'预计剩余 {int(max(0,total-done)/speed)} 秒' if speed>0 and done<total else ('下载完成，正在验证下载包' if done==total else '正在连接，等待传输数据')
            self.progress_detail.setText(f'{done/1048576:.1f} / {total/1048576:.1f} MiB · '+eta)
        else:
            self.bar.setValue(value.get('percent',0))
            if 'files_total' in value:
                self.metrics.setText(f"{value['files_done']} / {value['files_total']} 个文件")
                self.progress_detail.setText('本阶段 '+str(value.get('percent',0))+'% · 完成后仍需确认更新并重启')
            elif 'total_bytes' in value:
                self.metrics.setText(f"{value.get('processed_bytes',0)/1048576:.1f} / {value['total_bytes']/1048576:.1f} MiB")
                self.progress_detail.setText('本阶段 '+str(value.get('percent',0))+'% · 校验完成后继续准备安装')
            else:self.metrics.setText('正在处理…');self.progress_detail.clear()

    def download(self):
        if self.busy() or not can_install(self.parentWidget()):return
        if self.prepared:self.launch();return
        if self.info.get('operation')=='rollback' and QMessageBox.question(self,'确认回退',f'从 {APP_VERSION} 回退到 {self.info["version"]}？\n会备份设置并保留下载文件、账号及任务记录。')!=QMessageBox.StandardButton.Yes:return
        if self.need_confirm:
            if QMessageBox.question(self,'改用全量更新',self.selection['reason']+'。是否下载完整包？')!=QMessageBox.StandardButton.Yes:return
            self.info['allow_full']=True
        import updater
        self.cancel.clear();self.closing=False;self.stop.setText('取消并关闭');self.label.setText('开始准备更新…');self.steps.setText('当前阶段：检查本地文件');self.metrics.setText('正在准备…');self.bar.setValue(0)
        self.start(lambda progress:updater.prepare_update(self.info,self.cancel,progress),self.ready)
    def ready(self,result):
        if self.cancel.is_set():return
        self.prepared=result;self.awaiting_offer=True
        from update_pending import remember
        try:remember(result,self.info)
        except (OSError,ValueError,KeyError) as error:diagnostics.event('update.pending.save.failed',str(error))
        self.present_prepared()
    def present_prepared(self):
        self.version_label.setText(f"当前 {APP_VERSION} → 目标 {self.info['version']}")
        self.detail.setText('更新包已完整准备，尚未安装；稍后安装无需重复下载。')
        self.bar.setValue(100);self.steps.setText('当前阶段：待安装 · 版本尚未更新')
        self.label.setText('更新已下载并准备好，需要退出应用完成安装')
        self.metrics.setText(f"v{APP_VERSION} → v{self.info['version']}")
        self.progress_detail.setText('点击“更新并重启应用”完成更新。' if can_install(self.parentWidget()) else '当前有任务进行中，结束后可更新并重启。')
        self.stop.setText('关闭（稍后安装）');self.refresh()
        if self.scheduler:self.scheduler.refresh()
    def confirm_restart(self):
        prompt=QMessageBox(self);prompt.setWindowTitle('更新包已准备完成');prompt.setIcon(QMessageBox.Icon.Information)
        prompt.setText(f"更新 v{self.info['version']} 已下载并校验完成。\n需要退出应用以安装更新，完成后会自动重新启动。")
        now=prompt.addButton('立即更新并重启',QMessageBox.ButtonRole.AcceptRole)
        later=prompt.addButton('稍后安装',QMessageBox.ButtonRole.RejectRole);prompt.setDefaultButton(later)
        prompt.exec();return prompt.clickedButton()==now
    def offer_install(self):
        if not self.awaiting_offer or not self.isVisible() or not can_install(self.parentWidget()):return
        self.awaiting_offer=False
        if self.confirm_restart():self.launch()
    def restore_prepared(self,data):
        self.info=data['info'];self.prepared=data['prepared'];self.selection={'mode':'full','size':0,'reason':'已准备的更新包'};self.closing=False;self.cancel.clear();self.restored=True
        self.notes.render(self.info.get('notes',''));self.present_prepared()
        if data.get('error'):self.label.setText('上次安装未完成：'+data['error']);self.progress_detail.setText('更新文件已保留，可重试更新并重启或使用独立修复入口。')
    def launch(self):
        if self.launching:return
        if not can_install(self.parentWidget()):self.label.setText('更新包已准备好，请在当前任务结束后点击安装。');return
        import updater
        try:updater.launch_update(self.prepared);self.launching=True;self.steps.setText('当前阶段：退出、安装并重启');self.label.setText('正在退出应用以安装更新…')
        except Exception as error:self.failed(error);return
        parent=self.parentWidget()
        if parent and hasattr(parent,'request_exit'):parent.request_exit()
        else:
            if parent:parent.close()
            QApplication.instance().quit()
    def done_job(self):
        self.thread=None;self.job=None
        if self.closing:self.pending_info=None;self.close();return
        if self.pending_info is not None:
            info=self.pending_info;self.pending_info=None;self.receive(info);return
        if self.prepared and not self.cancel.is_set():self.present_prepared();self.offer_install()
        self.refresh()
    def failed(self,error):
        from update_package import FullConfirmation
        if isinstance(error,FullConfirmation):
            self.need_confirm=True;self.selection={'mode':'full','size':(self.info or {}).get('manifest',{}).get('size',0),'reason':str(error)};self.prepared=None
            self.detail.setText('需改用完整包 · '+str(error))
        self.label.setText('更新未完成：'+str(error)[:500]);self.bar.setRange(0,100)
    def snooze(self):
        if self.scheduler and not self.prepared:self.scheduler.snooze()
        self.close()
    def ignore_version(self):
        if self.scheduler and self.info:self.scheduler.ignore(self.info['version'])
        self.close()
    def reject(self):self.close()
    def closeEvent(self,event):
        if self.busy():self.closing=True;self.cancel.set();self.label.setText('正在停止更新请求…');event.ignore()
        else:event.accept()
