import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QWidget
from media_library import Catalogue
from library_ui import StorageDialog

class StoragePresentation(unittest.TestCase):
    def test_selected_storage_shows_capacity_and_registered_titles(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,WVD_DATA_DIR=folder):
            store=Catalogue()
            first=store.library(str(Path(folder)/'media'),'第一存储库')
            second=store.library(str(Path(folder)/'other'),'第二存储库')
            store.ingest({'site':'测试来源','key':'123','url':'https://example.com/123','title':'作品','entries':[{'key':'1','title':'第一集','url':'https://example.com/1'}]},'作品','第一季',second)
            dialog=StorageDialog(SimpleNamespace(window=QWidget(),store=store))
            self.assertIn('可用',dialog.capacity.text())
            self.assertIn('0',dialog.entries_heading.text())
            self.assertEqual(dialog.disks.count(),2)
            dialog.disks.setCurrentIndex(1)
            self.assertIn('1',dialog.entries_heading.text())
            self.assertIn('测试来源',dialog.entries.item(0).text())
            self.assertEqual(dialog.list.currentRow(),1)
            dialog.close()

