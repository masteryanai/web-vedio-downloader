import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
class StoragePresentation(unittest.TestCase):
    def run_widget_check(self,name):
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1')
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--gui-check',name],
            capture_output=True,text=True,encoding='utf-8',env=env,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)

    def test_registered_file_sizes_are_shown_without_scanning_unregistered_files(self):
        self.run_widget_check('test_registered_file_sizes_are_shown_without_scanning_unregistered_files')

    def test_selected_storage_shows_capacity_and_registered_titles(self):
        self.run_widget_check('test_selected_storage_shows_capacity_and_registered_titles')


def gui_check(name):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from PySide6.QtWidgets import QApplication,QWidget
    from media_library import Catalogue
    from library_ui import StorageDialog

    class StorageWidgetChecks(unittest.TestCase):
        def wait_for_capacity(self,app,dialog):
            deadline=time.monotonic()+5
            while '读取中' in dialog.capacity.text() and time.monotonic()<deadline:
                app.processEvents();time.sleep(.01)

        def test_registered_file_sizes_are_shown_without_scanning_unregistered_files(self):
            app=QApplication.instance() or QApplication([])
            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,WVD_DATA_DIR=folder):
                store=Catalogue();root=Path(folder)/'media';lib=store.library(str(root),'影片目录')
                _,episodes=store.ingest({'site':'测试来源','key':'123','url':'https://example.com/123','title':'作品','entries':[{'key':'1','title':'第一集','url':'https://example.com/1'}]},'作品','第一季',lib)
                media=root/'one.mp4';media.write_bytes(b'x'*4096);store.attach(episodes[0],media)
                (root/'not-registered.mp4').write_bytes(b'x'*8192)
                dialog=StorageDialog(SimpleNamespace(window=QWidget(),store=store));self.wait_for_capacity(app,dialog)
                row=dialog.entries.itemWidget(dialog.entries.item(0))
                self.assertEqual(row.size_label.text(),'4.0 KiB')
                self.assertIn('4.0 KiB',dialog.legend.text())
                self.assertIn('本库',dialog.legend.text())
                media.unlink();dialog.reload();self.wait_for_capacity(app,dialog)
                row=dialog.entries.itemWidget(dialog.entries.item(0))
                self.assertEqual(row.size_label.text(),'未知 / 离线')
                self.assertIn('1 个文件不可用',dialog.legend.toolTip())
                dialog.close()

        def test_selected_storage_shows_capacity_and_registered_titles(self):
            app=QApplication.instance() or QApplication([])
            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,WVD_DATA_DIR=folder):
                store=Catalogue()
                first=store.library(str(Path(folder)/'media'),'第一存储库')
                second=store.library(str(Path(folder)/'other'),'第二存储库')
                store.ingest({'site':'测试来源','key':'123','url':'https://example.com/123','title':'作品','entries':[{'key':'1','title':'第一集','url':'https://example.com/1'}]},'作品','第一季',second)
                dialog=StorageDialog(SimpleNamespace(window=QWidget(),store=store))
                self.wait_for_capacity(app,dialog)
                self.assertIn('可用',dialog.capacity.text())
                self.assertIn('0',dialog.entries_heading.text())
                self.assertEqual(dialog.disks.count(),2)
                dialog.disks.setCurrentIndex(1)
                self.assertIn('1',dialog.entries_heading.text())
                self.assertIn('测试来源',dialog.entries.item(0).text())
                self.assertEqual(dialog.list.currentRow(),1)
                dialog.close()


    result=unittest.TextTestRunner().run(unittest.TestSuite([StorageWidgetChecks(name)]))
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':
    if '--gui-check' in sys.argv:sys.exit(gui_check(sys.argv[-1]))
    unittest.main()
