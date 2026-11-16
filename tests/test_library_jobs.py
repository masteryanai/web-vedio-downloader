import os
import subprocess
import sys
import unittest


class LibraryJobs(unittest.TestCase):
    def test_success_and_failure_callbacks_are_on_gui_thread(self):
        code='''
import threading
from PySide6.QtCore import QCoreApplication,QTimer
from library_ui import Jobs
app=QCoreApplication([]);main=threading.get_ident();jobs=Jobs(None);seen=[];workers=[]
def action(cancel):workers.append(threading.get_ident());return 1
def failed_action(cancel):workers.append(threading.get_ident());raise ValueError('fixture')
def done(value):seen.append(('done',threading.get_ident()));assert value==1
def failed(value):seen.append(('failed',threading.get_ident()));assert value=='fixture'
jobs.start(action,done,failed);jobs.start(failed_action,done,failed)
timer=QTimer();timer.setInterval(10)
timer.timeout.connect(lambda:app.quit() if len(seen)==2 and not jobs.running else None);timer.start()
QTimer.singleShot(5000,app.quit);app.exec()
assert sorted(k for k,_ in seen)==['done','failed'],seen
assert all(t==main for _,t in seen),seen
assert all(t!=main for t in workers),workers
assert not jobs.running
assert jobs.shutdown()
'''
        env=dict(os.environ,PYTHONUTF8='1',PYTHONFAULTHANDLER='1')
        result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
