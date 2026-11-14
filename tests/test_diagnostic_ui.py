import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class DiagnosticUI(unittest.TestCase):
    def run_check(self,mode,timeout=15):
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,__file__,mode],env=dict(os.environ,LOCALAPPDATA=folder,QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1'),capture_output=True,text=True,encoding='utf-8',timeout=timeout)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_background_export_and_user_review_link(self):self.run_check('--export')
    def test_main_and_worker_exception_hooks_persist_redacted_traces(self):self.run_check('--exceptions')
    def test_real_independent_watchdog_when_gui_is_blocked(self):self.run_check('--hang',35)
    @unittest.skipUnless(os.name=='nt','Windows native GIL blockage')
    def test_independent_watchdog_survives_interpreter_blockage(self):self.run_check('--gil-hang',35)


def check_export():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from unittest.mock import patch
    from urllib.parse import urlsplit,parse_qs
    import time
    from PySide6.QtWidgets import QApplication,QFileDialog
    from PySide6.QtCore import QTimer
    import diagnostics as d
    from diagnostic_view import DiagnosticDialog
    app=QApplication([]);app.setQuitOnLastWindowClosed(False)
    folder=Path(os.environ['LOCALAPPDATA']);destination=folder/'report.zip'
    rec=d.start();dialog=DiagnosticDialog();dialog.description.setPlainText('Cookie: MY_COOKIE\nhttps://example.com/a?token=MY_TOKEN');dialog.show()
    ticks=[];timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:ticks.append(1));timer.start()
    original=d.export_bundle
    def slow(*args):time.sleep(.2);return original(*args)
    with patch.object(QFileDialog,'getSaveFileName',return_value=(str(destination),'')),patch('diagnostic_view.diagnostics.export_bundle',side_effect=slow),patch('diagnostic_view.QDesktopServices.openUrl',return_value=True) as opened:
        dialog.begin(True);deadline=time.monotonic()+5
        while dialog.thread is not None and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
        assert dialog.thread is None and destination.exists() and len(ticks)>5
        assert opened.call_count==1
        assert QFileDialog.getSaveFileName.call_args.kwargs['options'] & QFileDialog.Option.DontUseNativeDialog
        url=opened.call_args.args[0].toString();body=parse_qs(urlsplit(url).query)['body'][0]
        assert 'MY_COOKIE' not in body and 'MY_TOKEN' not in body and '手动添加' in body
        assert '提交' in dialog.status.text()
    dialog.close();rec.stop()


def check_exceptions():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import json,threading,time
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    import diagnostics as d
    app=QApplication([]);rec=d.start()
    def failed_worker():raise ValueError('password=SECRET_ONE SECRET_TWO')
    def failed_ui():raise RuntimeError('https://example.com/path?token=SECRET_THREE')
    child=threading.Thread(target=failed_worker);child.start();child.join()
    QTimer.singleShot(20,failed_ui);QTimer.singleShot(300,app.quit);app.exec();rec.stop()
    text=(d.root()/'runtime.log').read_text(encoding='utf-8')
    rows=[json.loads(line) for line in text.splitlines()]
    assert any(row['event']=='exception.main' for row in rows)
    assert any(row['event']=='exception.thread' for row in rows)
    assert 'failed_ui' in text and 'failed_worker' in text
    for secret in ('SECRET_ONE','SECRET_TWO','SECRET_THREE'):assert secret not in text


def check_hang(hold_gil=False):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import json,time
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    import diagnostics as d
    app=QApplication([]);rec=d.start();assert rec and rec.child
    heartbeat=QTimer();heartbeat.setInterval(100);heartbeat.timeout.connect(rec.beat);heartbeat.start()
    def blocking_ui():
        if hold_gil:
            import ctypes
            ctypes.PyDLL('kernel32').Sleep(17000)
        else:time.sleep(17)
    QTimer.singleShot(800,blocking_ui);QTimer.singleShot(19500,app.quit)
    app.exec();rec.stop()
    reports=[json.loads(p.read_text(encoding='utf-8')) for p in d.root().glob('incident-*.json')]
    assert reports and any(row['kind']=='ui_unresponsive' for row in reports)
    hang=next(row for row in reports if row['kind']=='ui_unresponsive')
    assert 'recovered_at' in hang
    if not hold_gil:assert 'blocking_ui' in json.dumps(hang)
    else:assert hang['last_snapshot']['threads'] and '最后采样' in hang['snapshot_note']
    print('Actual GUI blockage: independent watcher captured threads and recovery: PASS')


if __name__=='__main__':
    if '--export' in sys.argv:check_export()
    if '--exceptions' in sys.argv:check_exceptions()
    if '--hang' in sys.argv:check_hang()
    if '--gil-hang' in sys.argv:check_hang(True)
