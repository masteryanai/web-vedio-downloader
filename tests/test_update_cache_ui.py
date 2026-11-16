import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class CacheUI(unittest.TestCase):
    def test_background_statistics_path_change_and_confirmed_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,__file__,'--check'],env=dict(os.environ,LOCALAPPDATA=folder,WVD_DATA_DIR=str(Path(folder)/'WebVideoDownloaderPrototype'),QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1'),capture_output=True,text=True,encoding='utf-8',timeout=25)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

def check():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import time
    from unittest.mock import patch
    from PySide6.QtWidgets import QApplication,QMessageBox
    import update_cache as cache
    from update_cache_ui import CacheDialog
    import preferences
    with tempfile.TemporaryDirectory() as folder:
        install=Path(folder)/'app';install.mkdir();exe=install/'WebVideoDownloader.exe';exe.write_bytes(b'app')
        with patch('sys.frozen',True,create=True),patch('sys.executable',str(exe)):
            root=cache.register(cache.location());package=cache.package_dir(root,'a'*64);(package/'package.zip').write_bytes(b'x'*4096)
            app=QApplication([]);dialog=CacheDialog();dialog.show()
            def settle():
                deadline=time.monotonic()+10
                while dialog.thread is not None:
                    assert time.monotonic()<deadline,'cache UI worker timeout';app.processEvents();time.sleep(.005)
                app.processEvents()
            settle();assert '可清理' in dialog.usage.text() and dialog.clean.isEnabled()
            target=Path(folder)/'custom';dialog.path.setText(str(target));dialog.save.click();settle()
            assert preferences.load()['update_cache_dir']==str(target) and package.exists()
            with patch('update_cache_ui.QMessageBox.question',return_value=QMessageBox.StandardButton.No):dialog.clean.click()
            assert package.exists()
            with patch('update_cache_ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes):dialog.clean.click();settle()
            assert not package.exists() and '已清理' in dialog.status.text()
            dialog.close();app.processEvents()

if __name__=='__main__':
    if '--check' in sys.argv:check()
    else:unittest.main()
