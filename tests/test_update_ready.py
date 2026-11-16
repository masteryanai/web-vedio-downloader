import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ReadyUI(unittest.TestCase):
    def test_ready_update_waits_for_consent_and_survives_reopening(self):
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,__file__,'--check'],env=dict(os.environ,LOCALAPPDATA=folder,WVD_DATA_DIR=str(Path(folder)/'WebVideoDownloaderPrototype'),QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1'),capture_output=True,text=True,encoding='utf-8',timeout=20)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


def check():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from unittest.mock import patch,Mock
    import time
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication,QWidget,QPushButton
    from update_scheduler import UpdateScheduler
    from update_center import UpdateCenter
    from runtime import APP_VERSION
    app=QApplication([]);window=QWidget();window.update_button=QPushButton('检查更新',window);window.queue=SimpleNamespace(active={},waiting=[])
    window.resume_pending=[];window.is_busy=lambda:False;window.request_exit=Mock();window.desktop=SimpleNamespace(tray=None)
    scheduler=UpdateScheduler(window);window.update_scheduler=scheduler;window.show()
    info={'available':True,'version':'99.0.0','notes':'# Notes','manifest':{'schema':2,'size':100,'deltas':[{'from_version':APP_VERSION,'size':10}]}}
    def settle(condition):
        end=time.monotonic()+4
        while not condition():
            app.processEvents();time.sleep(.005);assert time.monotonic()<end,'worker not settled'
    with patch('updater.check_update',return_value=info),patch('update_package.verify_tree',side_effect=AssertionError('preview scanned all program files')):
        scheduler.manual();settle(lambda:scheduler.thread is None)
        dialog=window.update_dialog;assert dialog.selection['mode']=='delta' and dialog.install.isEnabled()
    dialog.transfer_progress({'phase':'baseline','percent':55,'stage':'检查本地文件','files_done':55,'files_total':100})
    assert dialog.bar.value()==55 and '55 / 100' in dialog.metrics.text()
    dialog.transfer_progress({'percent':47,'stage':'下载中','bytes':1048576,'total':2097152,'speed':1048576})
    assert dialog.bar.value()==50 and 'MiB/s' in dialog.metrics.text() and '预计剩余' in dialog.progress_detail.text()
    with patch('updater.prepare_update',return_value={'plan':'fixture','helper':'fixture'}) as prepare,patch.object(dialog,'confirm_restart',return_value=False) as consent,patch('updater.launch_update') as launch:
        dialog.install.click();settle(lambda:dialog.thread is None and dialog.prepared is not None)
        launch.assert_not_called();consent.assert_called_once();window.request_exit.assert_not_called()
        assert dialog.install.text()=='更新并重启应用' and window.update_button.text()=='更新并重启应用'
        assert '尚未安装' in dialog.detail.text() and '预计' not in dialog.detail.text()
        scheduler.checked({'available':False,'message':'already current'});assert dialog.info['version']=='99.0.0'
        dialog.snooze();assert not dialog.isVisible()
        with patch.object(scheduler,'check') as check_again:
            scheduler.manual();app.processEvents();check_again.assert_not_called()
        assert dialog.prepared and dialog.install.text()=='更新并重启应用';prepare.assert_called_once()
        window.queue.active={'work':1};dialog.refresh();assert not dialog.install.isEnabled();dialog.download();launch.assert_not_called()
        window.queue.active={};dialog.refresh();dialog.install.click();settle(lambda:dialog.thread is None and dialog.launching);launch.assert_called_once();window.request_exit.assert_called_once()
    dialog.close();window.request_exit.reset_mock()
    immediate=UpdateCenter(window);immediate.receive({'available':True,'version':'99.0.1'});immediate.show()
    with patch('updater.prepare_update',return_value={'plan':'fixture','helper':'fixture'}),patch.object(immediate,'confirm_restart',return_value=True) as consent,patch('updater.launch_update') as launch:
        immediate.download();settle(lambda:immediate.thread is None and immediate.launching)
        consent.assert_called_once();launch.assert_called_once();window.request_exit.assert_called_once()
    immediate.close();window.request_exit.reset_mock()
    failed=UpdateCenter(window);failed.receive({'available':True,'version':'99.0.2'});failed.prepared={'plan':'fixture'};failed.show()
    with patch('updater.launch_update',side_effect=RuntimeError('fixture handoff failed')):
        failed.launch();settle(lambda:failed.thread is None)
        window.request_exit.assert_not_called();assert not failed.launching and failed.install.isEnabled()
        assert 'fixture handoff failed' in failed.label.text()
    failed.close();scheduler.shutdown();window.close();app.processEvents()


if __name__=='__main__' and '--check' in sys.argv:check()
