import os
from pathlib import Path
import subprocess
import sys
import unittest


class UpdateDialog(unittest.TestCase):
    def test_progress_details_and_cancel_cross_thread(self):
        result=subprocess.run([sys.executable,__file__,'--check'],env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


def check():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from unittest.mock import patch
    from PySide6.QtWidgets import QApplication,QWidget
    from PySide6.QtCore import QTimer
    import updater
    app=QApplication([]);parent=QWidget();seen=[]
    def prepare(info,cancel,progress):
        progress({'percent':52,'stage':'连接中断，正在重试','bytes':1024*1024,'total':2*1024*1024,'speed':1024*1024})
        cancel.wait(5)
        raise RuntimeError('已取消更新下载，已保留进度')
    timer=QTimer();timer.setInterval(10)
    def drive():
        dialog=app.activeModalWidget()
        if not dialog:return
        if dialog.install.isEnabled() and not seen:dialog.install.click()
        if 'MiB/s' in dialog.label.text() and not seen:
            seen.append(dialog.label.text());assert dialog.bar.value()==52
            if os.environ.get('UPDATE_DIALOG_SCREENSHOT'):dialog.grab().save(os.environ['UPDATE_DIALOG_SCREENSHOT'])
            dialog.reject()
    timer.timeout.connect(drive);timer.start()
    QTimer.singleShot(8000,lambda:app.activeModalWidget().reject() if app.activeModalWidget() else None)
    with patch('updater.check_update',return_value={'available':True,'version':'9.0.0'}),patch('updater.prepare_update',side_effect=prepare),patch('updater.launch_update') as launch:
        updater.show_update_dialog(parent);launch.assert_not_called()
    assert seen and '重试' in seen[0] and '1.0 / 2.0 MiB' in seen[0]


if __name__=='__main__' and '--check' in sys.argv:check()
