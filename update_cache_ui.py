"""Background cache maintenance UI; never scan or delete on the Qt event thread."""
from PySide6.QtCore import QObject,QThread,Signal,Slot
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QFileDialog,QMessageBox
import update_cache as cache

class Work(QObject):
    success=Signal(object);error=Signal(str);finished=Signal()
    def __init__(self,action):super().__init__();self.action=action
    @Slot()
    def run(self):
        try:self.success.emit(self.action())
        except Exception as error:self.error.emit(str(error))
        finally:self.finished.emit()

class CacheDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('更新缓存');self.resize(620,360);self.thread=None;self.job=None
        box=QVBoxLayout(self);box.addWidget(QLabel('更新缓存目录（默认：程序根目录下的 update）'))
        self.path=QLineEdit(str(cache.location()));box.addWidget(self.path)
        row=QHBoxLayout();self.browse=QPushButton('选择目录');self.reset=QPushButton('恢复默认');self.save=QPushButton('应用目录');row.addWidget(self.browse);row.addWidget(self.reset);row.addWidget(self.save);box.addLayout(row)
        self.browse.clicked.connect(self.choose);self.reset.clicked.connect(lambda:self.path.setText(str(cache.installation()/'update')));self.save.clicked.connect(lambda:self.start(lambda:cache.change_location(self.path.text().strip())))
        self.usage=QLabel('正在统计…');self.usage.setWordWrap(True);box.addWidget(self.usage)
        note=QLabel('自动清理：闲置缓存最多保留 7 天、总量默认 1 GiB。\n正在更新、待安装及有效回退内容受保护，可能暂时超过上限。\n占用统计包含旧位置；仅清理已识别缓存，未知文件和回退备份不会删除。');note.setWordWrap(True);box.addWidget(note)
        row=QHBoxLayout();self.refresh=QPushButton('刷新占用');self.clean=QPushButton('清理闲置更新缓存');row.addWidget(self.refresh);row.addWidget(self.clean);box.addLayout(row)
        self.refresh.clicked.connect(lambda:self.start(cache.summary));self.clean.clicked.connect(self.cleanup)
        self.status=QLabel();self.status.setWordWrap(True);box.addWidget(self.status)
        self.close_button=QPushButton('关闭');self.close_button.clicked.connect(self.close);box.addWidget(self.close_button)
        self.start(cache.summary)
    def choose(self):
        value=QFileDialog.getExistingDirectory(self,'选择空目录或当前程序的缓存目录',self.path.text())
        if value:self.path.setText(value)
    def cleanup(self):
        if QMessageBox.question(self,'清理更新缓存','清理所有已识别的闲置更新缓存？\n未完成更新的闲置下载进度也会清除；正在使用、待安装、未知文件及回退备份会保留。')==QMessageBox.StandardButton.Yes:self.start(lambda:cache.cleanup(manual=True))
    def start(self,action):
        if self.thread is not None:return
        for button in (self.browse,self.reset,self.save,self.refresh,self.clean,self.close_button):button.setEnabled(False)
        self.status.setText('正在处理…');self.thread=QThread(self);self.job=Work(action);self.job.moveToThread(self.thread)
        self.thread.started.connect(self.job.run);self.job.success.connect(self.show_result);self.job.error.connect(self.status.setText);self.job.finished.connect(self.thread.quit);self.job.finished.connect(self.job.deleteLater);self.thread.finished.connect(self.worker_done);self.thread.finished.connect(self.thread.deleteLater);self.thread.start()
    def show_result(self,data):
        self.path.setText(data['path']);self.usage.setText(f"总占用：{data['total']/1024**2:.1f} MiB\n可清理：{data['reclaimable']/1024**2:.1f} MiB · 受保护：{data['protected']/1024**2:.1f} MiB\n未识别：{data['unknown']/1024**2:.1f} MiB（{data['unknown_count']} 项，保留）")
        self.status.setText(('已清理 %.1f MiB。'%(data['freed']/1024**2) if 'freed' in data else '统计完成。')+(' 部分文件清理失败，请稍后重试。' if data.get('errors') else ''))
    def worker_done(self):
        self.thread=None;self.job=None
        for button in (self.browse,self.reset,self.save,self.refresh,self.clean,self.close_button):button.setEnabled(True)
    def closeEvent(self,event):
        if self.thread is not None:event.ignore()
        else:super().closeEvent(event)
