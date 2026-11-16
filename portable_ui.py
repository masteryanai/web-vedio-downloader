"""Responsive first-launch data migration before normal application workers start."""
import os
import sys
import threading
from PySide6.QtCore import QObject,QThread,Signal,Slot
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QProgressBar,QPushButton,QMessageBox
from runtime import app_data_dir,legacy_data_dir
import portable_data

class MigrationWork(QObject):
    progress=Signal(str);success=Signal(object);failure=Signal(str);finished=Signal()
    def __init__(self,event):super().__init__();self.cancel_event=event
    @Slot()
    def run(self):
        try:self.success.emit(portable_data.initialize(self.cancel_event.is_set,self.progress.emit))
        except Exception as error:self.failure.emit(str(error))
        finally:self.finished.emit()

class MigrationDialog(QDialog):
    def __init__(self):
        super().__init__();self.setWindowTitle('迁移便携数据');self.resize(560,180);self.cancel_event=threading.Event();self.outcome=None;self.error=None;self.running=True
        box=QVBoxLayout(self);self.label=QLabel('正在迁移旧版数据到程序目录的 data，已下载的成品文件保持原位置。');self.label.setWordWrap(True);box.addWidget(self.label)
        bar=QProgressBar();bar.setRange(0,0);box.addWidget(bar);self.cancel=QPushButton('取消并退出');self.cancel.clicked.connect(self.cancel_event.set);box.addWidget(self.cancel)
        self.thread=QThread(self);self.work=MigrationWork(self.cancel_event);self.work.moveToThread(self.thread)
        self.thread.started.connect(self.work.run);self.work.progress.connect(self.label.setText);self.work.success.connect(self.received);self.work.failure.connect(self.failed);self.work.finished.connect(self.thread.quit);self.work.finished.connect(self.work.deleteLater);self.thread.finished.connect(self.completed);self.thread.start()
    def received(self,result):self.outcome=result
    def failed(self,error):self.error=error
    def completed(self):
        self.running=False;self.thread.wait();self.accept()
    def closeEvent(self,event):
        if self.running:self.cancel_event.set();event.ignore()
        else:super().closeEvent(event)

def prepare_data():
    marker=portable_data.read(app_data_dir()/portable_data.MARKER)
    needs=getattr(sys,'frozen',False) and not os.environ.get('WVD_DATA_DIR') and legacy_data_dir().exists() and marker.get('legacy_done') is not True
    try:
        if needs:
            dialog=MigrationDialog();dialog.exec()
            if dialog.cancel_event.is_set():return False
            if dialog.error:raise RuntimeError(dialog.error)
            result=dialog.outcome or {}
        else:result=portable_data.initialize()
        if not result.get('already_initialized') and (result.get('conflicts') or result.get('leftovers')):
            QMessageBox.information(None,'数据迁移完成','部分旧数据与现有数据不同或迁移时发生变化，已保留副本；详情见 data 中的迁移记录。')
        return True
    except (OSError,ValueError,RuntimeError) as error:
        QMessageBox.warning(None,'无法准备便携数据',str(error));return False
