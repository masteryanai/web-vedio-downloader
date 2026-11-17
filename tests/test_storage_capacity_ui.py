import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class StorageCapacityUI(unittest.TestCase):
    def test_latest_selection_and_close_discard_stale_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,WVD_DATA_DIR=folder,QT_QPA_PLATFORM='offscreen')
            result=subprocess.run([sys.executable,__file__,'--check'],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

def check():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import threading,time
    from types import SimpleNamespace
    from unittest.mock import patch
    from PySide6.QtWidgets import QApplication,QWidget
    from PySide6.QtTest import QTest
    from library_ui import StorageDialog
    from storage_presentation import storage_snapshot
    app=QApplication([])
    libraries=[{'id':str(i),'label':str(i),'path':str(Path(os.environ['WVD_DATA_DIR'])/str(i))} for i in range(3)]
    ws=SimpleNamespace(window=QWidget(),store=SimpleNamespace(read=lambda:{'libraries':libraries,'works':[],'episodes':[]},library_status=lambda key:{'writable':True,'pending':False,'state':'ready','error':''}))
    started=threading.Event();release=threading.Event();calls=[];active=[]
    def disk(path):
        calls.append(str(path));active.append(threading.get_ident())
        if len(calls)==1:started.set();assert release.wait(5)
        return SimpleNamespace(total=100,used=30,free=70)
    def wait(predicate):
        end=time.monotonic()+5
        while not predicate() and time.monotonic()<end:QTest.qWait(10)
        assert predicate()
    cancel=threading.Event();cancel.set()
    with patch('storage_presentation.shutil.disk_usage',side_effect=AssertionError('cancelled lookup')):
        assert storage_snapshot('unused',[],[],cancel) is None
    with patch('storage_presentation.shutil.disk_usage',side_effect=disk):
        dialog=StorageDialog(ws);dialog.show();wait(started.is_set)
        dialog.disks.setCurrentIndex(1);dialog.disks.setCurrentIndex(2)
        assert len(calls)==1
        release.set();wait(lambda:not dialog._capacity_running);app.processEvents()
        assert calls==[libraries[0]['path'],libraries[2]['path']],calls
        assert len(set(active))==1
        assert '70 B' in dialog.capacity.text()
        before=dialog.capacity.text()
        dialog.capacity_ready.emit(1,{'rows':{},'owned':0,'disk':None});app.processEvents()
        assert dialog.capacity.text()==before
        dialog.close()
    started.clear();release.clear();calls.clear()
    with patch('storage_presentation.shutil.disk_usage',side_effect=disk):
        dialog=StorageDialog(ws);dialog.show();wait(started.is_set)
        dialog.disks.setCurrentIndex(1);before=dialog.capacity.text();dialog.close();release.set()
        wait(lambda:not dialog._capacity_running);app.processEvents()
        assert len(calls)==1 and dialog.capacity.text()==before
        dialog.capacity_ready.emit(dialog._generation,{'rows':{},'owned':0,'disk':None});app.processEvents()
        assert dialog.capacity.text()==before
    print('storage latest request and close behavior passed')

if __name__=='__main__':
    if '--check' in sys.argv:check()
    else:unittest.main()
