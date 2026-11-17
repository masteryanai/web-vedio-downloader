"""Short, interruptible native feedback with a reduced-motion escape."""
import os
from PySide6.QtCore import Property,QPropertyAnimation,QEasingCurve,QEvent
from PySide6.QtGui import QPainter,QColor
from PySide6.QtWidgets import QPushButton
import preferences

def enabled():
    if preferences.load()['reduce_motion'] or os.environ.get('QT_QPA_PLATFORM')=='offscreen':return False
    if os.name=='nt':
        import ctypes
        value=ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042,0,ctypes.byref(value),0) and not value.value:return False
    return True

class FeedbackButton(QPushButton):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self._amount=0.;self.feedback=QPropertyAnimation(self,b'hoverAmount',self);self.feedback.setEasingCurve(QEasingCurve.Type.OutCubic)
    def amount(self):return self._amount
    def set_amount(self,value):self._amount=value;self.update()
    hoverAmount=Property(float,amount,set_amount)
    def event(self,event):
        if hasattr(self,'feedback') and event.type() in (QEvent.Type.Enter,QEvent.Type.Leave):
            target=1. if event.type()==QEvent.Type.Enter else 0.;self.feedback.stop()
            if enabled():self.feedback.setDuration(130 if target else 90);self.feedback.setStartValue(self._amount);self.feedback.setEndValue(target);self.feedback.start()
            else:self.set_amount(target)
        return super().event(event)
    def paintEvent(self,event):
        super().paintEvent(event)
        if self._amount and self.isEnabled():
            painter=QPainter(self);painter.setPen(__import__('PySide6.QtCore',fromlist=['Qt']).Qt.PenStyle.NoPen)
            painter.setBrush(QColor(102,192,244,int(18*self._amount)));painter.drawRoundedRect(self.rect(),3,3)

def reveal(dialog):
    previous=getattr(dialog,'_reveal_animation',None)
    if previous:previous.stop();previous.deleteLater()
    dialog.setWindowOpacity(1.)
    if not enabled():return
    animation=QPropertyAnimation(dialog,b'windowOpacity',dialog);animation.setDuration(160);animation.setStartValue(.96);animation.setEndValue(1.);animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    dialog._reveal_animation=animation;animation.start()
