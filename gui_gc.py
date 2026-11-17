"""Run cyclic collection on the Qt GUI thread, never an allocating worker."""
import gc
import time
from PySide6.QtCore import QObject,QTimer,QThread,Slot

class GuiCollector(QObject):
    def __init__(self,app,interval_ms=1000):
        super().__init__(app)
        self.was_enabled=gc.isenabled();self.last_full=time.monotonic();self.closed=False
        gc.disable()
        self.timer=QTimer(self);self.timer.setInterval(interval_ms);self.timer.timeout.connect(self.collect);self.timer.start()

    @Slot()
    def collect(self):
        if self.closed:return
        if QThread.currentThread()!=self.thread():raise RuntimeError('Qt循环引用必须在界面线程回收。')
        full=time.monotonic()-self.last_full>=30
        if full or gc.get_count()[0]>=gc.get_threshold()[0]:
            gc.collect(2 if full else 0)
            if full:self.last_full=time.monotonic()

    def close(self):
        if self.closed:return
        self.timer.stop();self.collect();self.closed=True
        if self.was_enabled:gc.enable()
