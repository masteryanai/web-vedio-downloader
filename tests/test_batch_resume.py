"""Exercise the actual window's resume workflow with simulated official captures."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class BatchResume(unittest.TestCase):
    def test_official_verification_is_serial_and_preserves_form(self):
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,LOCALAPPDATA=folder,QT_QPA_PLATFORM='offscreen')
            result=subprocess.run([sys.executable,__file__,'--check'],env=env,
                capture_output=True,text=True,encoding='utf-8',timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


def check():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from argparse import Namespace
    from unittest.mock import patch
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QApplication,QDialog,QMessageBox
    import main,download_tasks as tasks
    captures=[]
    class Capture(QDialog):
        captured=Signal(object)
        def __init__(self,*args):
            super().__init__(args[-1]);captures.append(self)
    def exercise():
        app=QApplication.instance()
        window=next(w for w in app.topLevelWidgets() if type(w).__name__=='Window')
        window.url_input.setPlainText('https://example.com/current-form')
        info={'title':'current selection','formats':[{'id':'source'}]}
        window.info=info
        made=[tasks.create({'url':f'https://www.douyin.com/note/{i}','site':'抖音','title':str(i)},'source',str(Path(os.environ['LOCALAPPDATA'])/'out')) for i in range(3)]
        for task in made:task['state']='paused';tasks.save(task)
        accepted=[]
        with patch.object(window.accounts,'_profile',return_value=None),patch.object(window.accounts,'_import_current'),patch.object(window.accounts,'cookies_for',return_value=[]),patch.object(window.queue,'enqueue',side_effect=lambda *args:accepted.append(args) or True):
            for task in made:assert window.resume_task(task['id'],False)
            assert not window.resume_task(made[0]['id'],False)
            app.processEvents();assert len(captures)==1
            with patch.object(QMessageBox,'information') as notice:
                window.check_updates();notice.assert_called_once()
            assert not window.close_when_idle and len(window.resume_pending)==2 and window.resume_browser is captures[0]
            captures[0].captured.emit({'formats':[{'id':'source'}]});captures[0].accept()
            app.processEvents();assert len(captures)==2 and len(accepted)==1
            # Cancel the second verification; it must not submit a download.
            captures[1].reject();app.processEvents();assert len(captures)==3 and len(accepted)==1
            # Cancellation from the task list archives the third before closing verification.
            window.queue.discard(made[2]['id']);app.processEvents()
            assert len(accepted)==1 and window.resume_browser is None
            assert window.url_input.toPlainText()=='https://example.com/current-form' and window.info is info
            assert not window.resume_pending
        window.close();return 0
    with patch('douyin_browser.DouyinCapture',Capture),patch.object(QApplication,'exec',side_effect=exercise):
        assert main.gui(Namespace(smoke_ui=False,url=None))==0


if __name__=='__main__' and '--check' in sys.argv:check()
