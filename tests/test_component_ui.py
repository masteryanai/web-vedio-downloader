import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from component_ui import ensure_browser
from gui_gc import GuiCollector

class ComponentUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_existing_component_verification_keeps_gui_heartbeat_running(self):
        ticks=[];threads=[];timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(True));timer.start();owner=threading.get_ident();collector=GuiCollector(self.app)
        def verify(cancel,progress):
            threads.append(threading.get_ident())
            for n in range(8):
                time.sleep(.02);progress({'stage':'校验已有组件','processed_bytes':n+1,'total_bytes':8})
        try:
            with tempfile.TemporaryDirectory() as folder,patch('component_ui.browser_ready',return_value=False),patch('component_ui.descriptor',return_value={'size':100}),patch('component_ui.folder',return_value=Path(folder)),patch('component_ui.activate_browser',side_effect=verify),patch('component_ui.fetch_browser',side_effect=AssertionError('existing component must not download')):
                self.assertTrue(ensure_browser())
            self.assertGreaterEqual(len(ticks),8);self.assertEqual(len(threads),1);self.assertNotEqual(threads[0],owner)
        finally:timer.stop();collector.close()

if __name__=='__main__':unittest.main()
