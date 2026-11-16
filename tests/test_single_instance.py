import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]


class SingleInstance(unittest.TestCase):
    def test_second_launch_restores_existing_window_without_constructing_another(self):
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,LOCALAPPDATA=folder,WVD_DATA_DIR=str(Path(folder)/'WebVideoDownloaderPrototype'),QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1',PYTHONPATH=str(ROOT))
            first=subprocess.Popen([sys.executable,__file__,'--primary',folder],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+8
                while not (Path(folder)/'ready').exists():
                    self.assertIsNone(first.poll(),'primary exited before ready')
                    self.assertLess(time.monotonic(),deadline,'primary not ready');time.sleep(.02)
                second=subprocess.run([sys.executable,__file__,'--secondary',folder],env=env,capture_output=True,text=True,encoding='utf-8',timeout=8)
                self.assertEqual(second.returncode,0,second.stdout+second.stderr)
                self.assertFalse((Path(folder)/'secondary-window').exists(),'再次启动创建了第二个主窗口和托盘')
                deadline=time.monotonic()+3
                while not (Path(folder)/'activated').exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue((Path(folder)/'activated').exists(),'未唤起已隐藏的原窗口')
                self.assertTrue((Path(folder)/'preserved').exists(),'再次启动覆盖了原窗口输入')
                (Path(folder)/'activated').unlink();(Path(folder)/'minimize').touch()
                deadline=time.monotonic()+3
                while not (Path(folder)/'minimized').exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue((Path(folder)/'minimized').exists())
                third=subprocess.run([sys.executable,__file__,'--secondary',folder],env=env,capture_output=True,text=True,encoding='utf-8',timeout=8)
                self.assertEqual(third.returncode,0,third.stdout+third.stderr)
                deadline=time.monotonic()+3
                while not (Path(folder)/'activated').exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue((Path(folder)/'activated').exists(),'最小化窗口未恢复')
            finally:
                (Path(folder)/'quit').touch()
                try:first.communicate(timeout=8)
                except subprocess.TimeoutExpired:first.kill();first.communicate()


    def test_crashed_owner_does_not_leave_an_unusable_instance_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,LOCALAPPDATA=folder,WVD_DATA_DIR=str(Path(folder)/'WebVideoDownloaderPrototype'),QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1',PYTHONPATH=str(ROOT))
            first=subprocess.Popen([sys.executable,__file__,'--primary',folder],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+8
                while not (Path(folder)/'ready').exists():
                    self.assertIsNone(first.poll());self.assertLess(time.monotonic(),deadline);time.sleep(.02)
            finally:first.kill();first.communicate(timeout=8)
            second=subprocess.run([sys.executable,__file__,'--secondary',folder],env=env,capture_output=True,text=True,encoding='utf-8',timeout=8)
            self.assertEqual(second.returncode,0,second.stdout+second.stderr)
            self.assertTrue((Path(folder)/'secondary-window').exists(),'原进程异常退出后无法重新启动')
            self.assertFalse((Path(folder)/'WebVideoDownloaderPrototype/instance.lock').exists())

    def test_simultaneous_startup_constructs_only_one_window(self):
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,LOCALAPPDATA=folder,WVD_DATA_DIR=str(Path(folder)/'WebVideoDownloaderPrototype'),QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1',PYTHONPATH=str(ROOT))
            children=[subprocess.Popen([sys.executable,__file__,'--race',folder],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
            try:
                for process in children:
                    out,err=process.communicate(timeout=12);self.assertEqual(process.returncode,0,(out+err).decode('utf-8',errors='replace'))
                self.assertEqual(len(list(Path(folder).glob('owner-*'))),1,'同时启动产生了两个 GUI 实例')
            finally:
                for process in children:
                    if process.poll() is None:process.kill();process.communicate()


def control(window,folder):
    if (folder/'quit').exists():window.request_exit()
    if (folder/'minimize').exists():
        (folder/'minimize').unlink();window.showMinimized();(folder/'minimized').touch()

def child(mode,folder):
    sys.path.insert(0,str(ROOT))
    from argparse import Namespace
    from unittest.mock import patch
    from PySide6.QtWidgets import QApplication,QSystemTrayIcon
    from PySide6.QtCore import QTimer
    import main,preferences
    preferences.update(auto_check_updates=False)
    original=QApplication.exec;timers=[]
    def execute():
        app=QApplication.instance();window=next(w for w in app.topLevelWidgets() if type(w).__name__=='Window')
        if mode=='--race':
            (folder/f'owner-{os.getpid()}').touch();QTimer.singleShot(2000,window.request_exit)
        elif mode=='--secondary':
            (folder/'secondary-window').touch();QTimer.singleShot(30,window.request_exit)
        else:
            window.url_input.setPlainText('keep original input')
            restore=window.desktop.restore_main
            def activated():
                restore()
                if window.isVisible() and not window.isMinimized():(folder/'activated').touch()
                if window.url_input.toPlainText()=='keep original input':(folder/'preserved').touch()
            window.desktop.restore_main=activated
            window.desktop.hide_main();(folder/'ready').touch()
            timer=QTimer();timer.setInterval(20);timer.timeout.connect(lambda:control(window,folder));timer.start();timers.append(timer)
        return original()
    with patch.object(QApplication,'exec',side_effect=execute),patch.object(QSystemTrayIcon,'isSystemTrayAvailable',return_value=True),patch('diagnostics.start',return_value=None),patch('diagnostics.unreported',return_value=[]):
        return main.gui(Namespace(smoke_ui=False,url='https://example.invalid/secondary' if mode=='--secondary' else None))


if __name__=='__main__':
    if len(sys.argv)>2 and sys.argv[1] in ('--primary','--secondary','--race'):sys.exit(child(sys.argv[1],Path(sys.argv[2])))
    unittest.main()
