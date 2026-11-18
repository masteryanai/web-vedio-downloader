"""Explicit consent and responsive progress before optional browser acquisition."""
import threading
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QProgressBar,QPushButton,QMessageBox
from library_ui import Jobs
from optional_components import activate_browser,browser_ready,descriptor,folder,fetch_browser,ComponentRequired

def ensure_browser(parent=None):
    if browser_ready():
        try:activate_browser();return True
        except (OSError,ValueError,ImportError) as error:
            QMessageBox.warning(parent,'浏览器组件不可用',str(error));return False
    try:value=descriptor()
    except (OSError,ValueError,ComponentRequired) as error:
        QMessageBox.warning(parent,'需要浏览器组件',str(error));return False
    installed=folder(value).is_dir()
    if not installed and QMessageBox.question(parent,'下载浏览器验证组件',
            f"此操作需要官方网页登录 / 抖音解析组件。\n下载大小：{value['size']/1048576:.1f} MiB。\n"
            '从GitHub发布页获取并校验，保存到本程序目录，不安装到系统。也可使用离线完整包。是否下载？')!=QMessageBox.StandardButton.Yes:return False
    dialog=QDialog(parent);dialog.setWindowTitle('准备浏览器组件');dialog.resize(540,190);box=QVBoxLayout(dialog)
    label=QLabel('正在校验已有组件…' if installed else '正在下载组件…');label.setWordWrap(True);box.addWidget(label);bar=QProgressBar();bar.setRange(0,0);box.addWidget(bar)
    cancel=QPushButton('取消，保留当前任务');box.addWidget(cancel);jobs=Jobs(dialog);finished=[];stopped=[False]
    def progress(data):
        # Worker emits through a QObject-owned GUI callback below.
        relay.changed.emit(data)
    from PySide6.QtCore import QObject,Signal,Slot,QTimer
    class Relay(QObject):
        changed=Signal(object)
        @Slot(object)
        def receive(self,data):
            label.setText(data.get('stage','正在下载组件…'))
            done,total=data.get('bytes',data.get('processed_bytes',data.get('files_done'))),data.get('total',data.get('total_bytes',data.get('files_total')))
            if total and done is not None:bar.setRange(0,100);bar.setValue(int(done*100/total))
    relay=Relay(dialog);relay.changed.connect(relay.receive)
    def done(_):finished.append(True);stopped[0]=True;dialog.accept()
    def fail(error):stopped[0]=True;label.setText('组件未就绪：'+error);cancel.setText('关闭')
    def prepare(event):
        if not installed:fetch_browser(event,progress)
        activate_browser(event,progress)
    token=jobs.start(prepare,done,fail)
    def cancel_or_close():
        if stopped[0]:dialog.reject()
        else:token.set();cancel.setEnabled(False);label.setText('正在停止，当前任务保留…')
    cancel.clicked.connect(cancel_or_close)
    # Closing waits for workers; no QThread can outlive its owner.
    def close_check():
        if stopped[0] and not jobs.running and not finished:cancel.setEnabled(True)
    timer=QTimer(dialog);timer.timeout.connect(close_check);timer.start(100)
    dialog.exec()
    if jobs.running:
        if not finished:token.set()
        from PySide6.QtWidgets import QApplication
        import time
        while jobs.running:QApplication.processEvents();time.sleep(.01)
    if not finished or token.is_set():return False
    return True
