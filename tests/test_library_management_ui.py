"""Actual Qt workflow checks, isolated from QCoreApplication test processes."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LibraryManagementUI(unittest.TestCase):
    def test_sources_recovery_requires_matching_identity_and_all_existing_episodes(self):
        import threading
        from library_management import verify_sources
        group={'site':'demo','key':'season','url':'https://example.org/season','entries':[{'key':'one'}]}
        data={'works':[{'id':'w','library_id':'l','title':'Title','default_source':'demo|season',
             'sources':[{'site':'demo','key':'demo|season','url':'https://example.org/season'}]}],
             'episodes':[{'work_id':'w','sources':[{'source':'demo|season','key':'one'}]}]}
        def verify(value):
            return verify_sources(data,'l',{},threading.Event(),discover=lambda *args:value)
        self.assertEqual(len(verify(group)['checked']),1)
        self.assertTrue(verify(dict(group,key='different'))['failures'])
        self.assertTrue(verify(dict(group,entries=[]))['failures'])
        self.assertEqual(data['works'][0]['id'],'w')

    def test_add_recover_move_and_rebuild_without_redownload(self):
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,WVD_DATA_DIR=str(Path(folder)/'data'),LOCALAPPDATA=str(Path(folder)/'legacy'),QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1')
            result=subprocess.run([sys.executable,__file__,'--check'],env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


def check():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import time,traceback
    from argparse import Namespace
    from unittest.mock import patch
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication,QMessageBox,QFileDialog
    import main
    from library_ui import StorageDialog
    import download_tasks as tasks
    original=QApplication.exec;errors=[];completed=[]
    root=Path(os.environ['WVD_DATA_DIR']).parent
    group={'site':'测试','key':'season','url':'https://example.com/season','title':'恢复试验','season':'第一季','entries':[{'key':'1','title':'第一集','url':'https://example.com/1'}]}

    def execute():
        app=QApplication.instance();window=next(w for w in app.topLevelWidgets() if type(w).__name__=='Window');ws=window.workspace

        def wait(predicate):
            deadline=time.monotonic()+12
            while not predicate() and time.monotonic()<deadline:QApplication.processEvents();time.sleep(.01)
            assert predicate(),'GUI operation timed out'

        def drive():
            try:
                source=root/'source';lib=ws.store.library(source,'测试库');wid,eps=ws.store.ingest(group,'恢复试验','第一季',lib)
                file=source/'episodes'/'one.mp4';file.parent.mkdir();file.write_bytes(b'existing media sentinel')
                ws.store.attach(eps[0],file);ws.store.patch_work(wid,no_play_prompt=True,check_enabled=True)
                task=tasks.create({'url':'https://example.com/2','site':'测试','title':'第二集'},'test',source/'episodes')
                cache=tasks.work_path(task);cache.mkdir();(cache/'source.part').write_bytes(b'keep partial bytes')
                ws.store.persist_library(lib)
                ws.store.remove_library(lib)
                dialog=StorageDialog(ws);dialog.show();manager=dialog.manager
                with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes),patch.object(QMessageBox,'information',return_value=QMessageBox.StandardButton.Ok),patch.object(QMessageBox,'warning',side_effect=lambda *args:errors.append(str(args))):
                    manager.add(str(source));wait(lambda:not manager.busy)
                    assert ws.store.work(wid)['no_play_prompt'] and ws.store.work(wid)['check_enabled']
                    assert not window.queue.active and not window.queue.waiting
                    destination=root/'destination';destination.mkdir()
                    with patch.object(QFileDialog,'getExistingDirectory',return_value=str(destination)):
                        manager.move();wait(lambda:not manager.busy)
                    assert Path(ws.store.read()['libraries'][0]['path'])==destination
                    assert (destination/'episodes/one.mp4').read_bytes()==b'existing media sentinel'
                    assert (destination/'episodes'/cache.name/'source.part').read_bytes()==b'keep partial bytes'
                    assert Path(tasks.load(task['id'])['output'])==destination/'episodes'
                    assert not source.exists() or not any(source.rglob('one.mp4'))
                    ws.store.path.write_bytes(b'{broken central index')
                    manager.repair_index();wait(lambda:not manager.busy)
                    assert list((ws.store.path.parent/'quarantine').glob('catalogue-*.json'))
                    manager.add(str(destination));wait(lambda:not manager.busy)
                    assert ws.store.work(wid)['title']=='恢复试验'
                    assert not window.queue.active and not window.queue.waiting
                dialog.close();completed.append(True)
            except BaseException:errors.append(traceback.format_exc())
            window.request_exit()

        QTimer.singleShot(100,drive);QTimer.singleShot(45000,window.request_exit)
        return original()

    with patch.object(QApplication,'exec',side_effect=execute):
        main.gui(Namespace(smoke_ui=False,url=None,screenshot=None))
    assert not errors,errors
    assert completed



if __name__=='__main__':
    if '--check' in sys.argv:
        try:check()
        except BaseException:
            import traceback
            traceback.print_exc(file=sys.__stderr__);raise SystemExit(1)
    else:unittest.main()

