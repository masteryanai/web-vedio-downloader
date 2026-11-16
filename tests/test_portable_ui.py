import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class MigrationUI(unittest.TestCase):
    def test_worker_success_and_cancel_keep_event_loop_responsive(self):
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,__file__,'--check'],env=dict(os.environ,WVD_DATA_DIR=folder,QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1'),capture_output=True,text=True,encoding='utf-8',timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

def check():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import time
    from unittest.mock import patch
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from portable_ui import MigrationDialog
    app=QApplication([])
    def slow(cancel,progress):
        for _ in range(30):
            if cancel():raise RuntimeError('cancelled')
            progress('working');time.sleep(.005)
        return {'migrated':2}
    with patch('portable_ui.portable_data.initialize',side_effect=slow):
        dialog=MigrationDialog();ticks=[];timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(1));timer.start();dialog.exec();timer.stop()
        assert dialog.outcome['migrated']==2 and len(ticks)>3 and not dialog.running
        dialog.deleteLater();app.processEvents()
        dialog=MigrationDialog();QTimer.singleShot(30,dialog.cancel_event.set);dialog.exec();assert dialog.error=='cancelled' and not dialog.running
        dialog.deleteLater();app.processEvents()

if __name__=='__main__':
    if '--check' in sys.argv:check()
    else:unittest.main()
