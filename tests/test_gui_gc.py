import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import gc,threading,unittest,weakref
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtTest import QTest
from gui_gc import GuiCollector

class GuiCollection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_worker_allocations_do_not_collect_widget_cycles(self):
        initial=gc.isenabled();collector=GuiCollector(self.app,10);released=[];owner=threading.get_ident()
        class Window(QWidget):
            def __del__(self):released.append(threading.get_ident())
        widget=Window();widget.cycle=widget;reference=weakref.ref(widget);del widget
        try:
            self.assertFalse(gc.isenabled())
            def allocate():
                for i in range(5000):value=[{} for _ in range(8)]
            worker=threading.Thread(target=allocate);worker.start();worker.join()
            self.assertIsNotNone(reference());self.assertFalse(released)
            collector.last_full-=31;QTest.qWait(40)
            self.assertIsNone(reference());self.assertEqual(released,[owner])
        finally:collector.close()
        self.assertEqual(gc.isenabled(),initial)

if __name__=='__main__':unittest.main()
