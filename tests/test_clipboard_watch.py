import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PySide6.QtWidgets import QApplication,QWidget,QDialog
from clipboard_watch import ClipboardWatcher
from clipboard_links import SeenLinks

class ClipboardScheduling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_deferred_link_is_not_consumed_until_main_is_foreground(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,WVD_DATA_DIR=str(Path(folder)/'data')):
            main=QWidget();dialog=QDialog();flow=Mock();main.close_when_idle=False;main.is_busy=lambda:False
            main.workspace=types.SimpleNamespace(add_dialog=dialog,add_flow=flow)
            watcher=ClipboardWatcher(main);watcher.retry.stop();url='https://www.bilibili.com/video/BV1xx411c7mD';watcher.pending=[url]
            with patch.object(QApplication,'activeWindow',return_value=dialog):watcher.present()
            self.assertEqual(watcher.pending,[url]);self.assertEqual(watcher.seen.unseen([url]),[url]);flow.start_urls.assert_not_called()
            with patch.object(QApplication,'activeWindow',return_value=main),patch.object(QApplication,'activeModalWidget',return_value=None),patch.object(QApplication,'topLevelWidgets',return_value=[]):watcher.present()
            flow.start_urls.assert_called_once_with([url],automatic=True);self.assertFalse(SeenLinks().unseen([url]));main.deleteLater();dialog.deleteLater()

    def test_cancelled_multi_links_are_deduplicated_without_starting_download(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,WVD_DATA_DIR=str(Path(folder)/'data')):
            main=QWidget();dialog=QDialog();main.close_when_idle=False;main.is_busy=lambda:False;flow=Mock()
            main.workspace=types.SimpleNamespace(add_dialog=dialog,add_flow=flow);watcher=ClipboardWatcher(main);watcher.retry.stop()
            urls=['https://www.bilibili.com/video/BV1xx411c7mD','https://www.bilibili.com/video/BV1xx411c7mE'];watcher.pending=urls
            with patch.object(QApplication,'activeWindow',return_value=main),patch.object(QApplication,'activeModalWidget',return_value=None),patch.object(QApplication,'topLevelWidgets',return_value=[]),patch('clipboard_watch.choose_links',return_value=[]):watcher.present()
            flow.start_urls.assert_not_called();self.assertFalse(SeenLinks().unseen(urls));self.assertFalse(watcher.pending);main.deleteLater();dialog.deleteLater()

if __name__=='__main__':unittest.main()
