"""One shared check worker for manual checks, startup and tray polling."""
import time
from PySide6.QtCore import QObject,QTimer,QThread,Signal,Slot,Qt,QEvent
from PySide6.QtWidgets import QLabel
import preferences
import update_state as state_store
import diagnostics


class CheckJob(QObject):
    success=Signal(object);failure=Signal(str);finished=Signal()
    @Slot()
    def run(self):
        try:
            import updater
            self.success.emit(updater.check_update())
        except Exception as error:diagnostics.exception('update.check.failed',error,error.__traceback__);self.failure.emit(str(error))
        finally:self.finished.emit()


class UpdateScheduler(QObject):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.state=state_store.load();self.thread=None;self.job=None;self.stopping=False
        self.badge=QLabel('●',window.update_button);self.badge.setStyleSheet('color:#ee4545;background:transparent;font-size:13px;');self.badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        window.update_button.installEventFilter(self);self.refresh()
        self.timer=QTimer(self);self.timer.setInterval(60000);self.timer.timeout.connect(self.tick);self.timer.start()
        QTimer.singleShot(15000,self.tick)
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Type.Resize:self.badge.move(obj.width()-16,2)
        return False
    def refresh(self):
        show=state_store.visible(self.state);self.badge.setVisible(show);self.badge.move(self.window.update_button.width()-16,2)
        self.window.update_button.setText('发现 v'+self.state['known']['version'] if show else '检查更新')
        try:state_store.save(self.state)
        except OSError as error:diagnostics.event('update.state.write.failed',str(error))
    def tick(self):
        if self.stopping:return
        cfg=preferences.load()
        if state_store.due(self.state,cfg['auto_check_updates'],cfg['update_interval_hours']):self.check()
        self.notify()
    def notify(self):
        if not state_store.visible(self.state) or time.time()<self.state.get('snooze_until',0):return
        ver=self.state['known']['version']
        if self.state.get('notified_version')==ver:return
        tray=getattr(getattr(self.window,'desktop',None),'tray',None)
        if tray and tray.isVisible():
            tray.showMessage('发现新版本','Web Video Downloader v'+ver+'，请打开“检查更新”查看。')
            self.state['notified_version']=ver;self.refresh()
    def check(self):
        if self.thread is not None or self.stopping:return
        self.thread=QThread(self);self.job=CheckJob();self.job.moveToThread(self.thread)
        self.thread.started.connect(self.job.run);self.job.success.connect(self.checked);self.job.failure.connect(self.failed)
        self.job.finished.connect(self.thread.quit);self.job.finished.connect(self.job.deleteLater)
        self.thread.finished.connect(self.done);self.thread.finished.connect(self.thread.deleteLater);self.thread.start()
    def checked(self,info):
        state_store.succeeded(self.state,info);self.refresh();self.notify()
        dialog=getattr(self.window,'update_dialog',None)
        if dialog and not dialog.busy() and not (dialog.info or {}).get('operation')=='rollback':dialog.receive(info)
    def failed(self,error):
        state_store.failed(self.state);self.refresh()
        dialog=getattr(self.window,'update_dialog',None)
        if dialog and dialog.isVisible() and not dialog.busy():dialog.failed(error)
    def done(self):self.thread=None;self.job=None
    def manual(self):
        from update_center import UpdateCenter
        dialog=getattr(self.window,'update_dialog',None)
        if dialog is None:
            dialog=UpdateCenter(self.window,self);self.window.update_dialog=dialog
        dialog.show();dialog.raise_();dialog.activateWindow()
        if not dialog.busy():
            if state_store.visible(self.state):dialog.receive(self.state['known'])
            self.check()
    def ignore(self,ver):self.state['ignored_version']=ver;self.refresh()
    def snooze(self):self.state.update(snooze_until=time.time()+4*3600,notified_version='');self.refresh()
    def shutdown(self):
        self.stopping=True;self.timer.stop()
        dialog=getattr(self.window,'update_dialog',None)
        if dialog:dialog.close()
        return self.thread is None and (not dialog or not dialog.busy())
