import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class UpdateCenterUI(unittest.TestCase):
    def test_polling_badge_markdown_busy_gate_and_shared_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,__file__,'--check'],env=dict(os.environ,LOCALAPPDATA=folder,QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1'),capture_output=True,text=True,encoding='utf-8',timeout=20)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


def check():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import threading,time
    from types import SimpleNamespace
    from unittest.mock import patch
    from PySide6.QtWidgets import QApplication,QWidget,QPushButton
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QTextFormat
    from update_scheduler import UpdateScheduler
    from update_center import Notes,can_install
    import preferences
    app=QApplication([]);window=QWidget();window.resize(500,200);window.update_button=QPushButton('检查更新',window);window.update_button.resize(180,40)
    window.queue=SimpleNamespace(active={},waiting=[]);window.resume_pending=[];window.is_busy=lambda:False
    tray=SimpleNamespace(isVisible=lambda:True,showMessage=lambda *args:notices.append(args));window.desktop=SimpleNamespace(tray=tray)
    notices=[];scheduler=UpdateScheduler(window);window.show();app.processEvents();assert not scheduler.badge.isVisible()
    event=threading.Event();result={'available':True,'version':'99.0.0','notes':'# 标题\n\n**粗体**\n\n|甲|乙|\n|---|---|\n|1|2|'}
    def fetch():event.wait(2);return result
    def settle(condition):
        end=time.monotonic()+4
        while not condition():
            app.processEvents();time.sleep(.005)
            assert time.monotonic()<end,'UI worker did not settle'
    with patch('updater.check_update',side_effect=fetch) as request:
        scheduler.check();scheduler.check();scheduler.manual();assert request.call_count<=1;event.set();settle(lambda:scheduler.thread is None)
        dialog=window.update_dialog;settle(lambda:dialog.install.isEnabled());assert request.call_count==1
        assert scheduler.badge.isVisible() and '99.0.0' in window.update_button.text() and len(notices)==1
        assert '标题' in dialog.notes.toPlainText() and '# 标题' not in dialog.notes.toPlainText() and '**' not in dialog.notes.toPlainText()
        assert '<table' in dialog.notes.document().toHtml();assert '<h1' in dialog.notes.document().toHtml()
        dialog.close();assert scheduler.badge.isVisible();scheduler.notify();assert len(notices)==1
        scheduler.snooze();scheduler.notify();assert len(notices)==1 and scheduler.badge.isVisible()
        scheduler.ignore('99.0.0');assert not scheduler.badge.isVisible()
        scheduler.checked({'available':True,'version':'99.0.1'});assert scheduler.badge.isVisible()
        scheduler.failed('offline');assert scheduler.badge.isVisible()
    entries=[{'version':ver,'rollback_allowed':allowed,'package':{},'inventory':{},'data_schema':1,'notes':'# History'} for ver,allowed in [('0.8.0',True),('0.7.0',False),('0.6.2',True)]]
    dialog.catalog_manifest={'version':'99.0.1'}
    with patch('update_package.history',return_value=entries),patch('update_package.select',return_value={'mode':'full','size':100,'reason':'历史完整包'}):
        dialog.show();dialog.load_history();settle(lambda:not dialog.busy())
        assert dialog.versions.count()==4 and not dialog.versions.model().item(2).isEnabled()
        dialog.versions.setCurrentIndex(1);settle(lambda:not dialog.busy())
        assert dialog.info['operation']=='rollback' and '回退' in dialog.install.text() and not dialog.ignore.isEnabled()
    window.queue.active={'task':1};assert not can_install(window)
    window.queue.active={};assert can_install(window)
    assert preferences.load()['auto_check_updates'] and preferences.load()['update_interval_hours']==4
    preferences.update(auto_check_updates=False,update_interval_hours=6);assert not preferences.load()['auto_check_updates']
    assert scheduler.shutdown();window.close();app.processEvents()


if __name__=='__main__' and '--check' in sys.argv:check()
