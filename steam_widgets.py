"""Steam-like window chrome and cover rendering, independent of catalogue logic."""
from PySide6.QtCore import Qt, QObject, QEvent, QSize
from PySide6.QtGui import QColor, QPainter, QLinearGradient, QFont, QFontMetrics, QIcon, QPixmap, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QStyledItemDelegate, QStyle, QApplication, QMenu

def control_icon(kind):
    """Draw monochrome window controls at device-independent size."""
    pixmap=QPixmap(32,32);pixmap.fill(Qt.GlobalColor.transparent);pixmap.setDevicePixelRatio(2)
    painter=QPainter(pixmap);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#a8b0ba'),1.2))
    if kind=='minimize':painter.drawLine(3,11,13,11)
    elif kind=='maximize':painter.drawRect(3,3,9,9)
    elif kind=='close':painter.drawLine(3,3,12,12);painter.drawLine(12,3,3,12)
    elif kind=='back':painter.drawLine(13,8,3,8);painter.drawLine(3,8,7,4);painter.drawLine(3,8,7,12)
    elif kind=='add':
        painter.setPen(QPen(QColor('#e4edf5'),1.8));painter.drawRoundedRect(1,1,14,14,2,2)
        painter.drawLine(4,8,12,8);painter.drawLine(8,4,8,12)
    painter.end();return QIcon(pixmap)


class Chrome(QFrame):
    def __init__(self, window):
        super().__init__(); self.window=window; self.setObjectName('windowChrome'); self.setFixedHeight(29)
        row=QHBoxLayout(self);row.setContentsMargins(12,0,0,0);row.setSpacing(15)
        logo=QLabel();logo.setPixmap(window.windowIcon().pixmap(14,14));row.addWidget(logo);row.addWidget(QLabel('WVD'))
        for title,actions in [('查看',[('影片库',lambda:window.workspace.home()),('下载任务',lambda:window.workspace.show_tasks()),('设置',window.show_settings)]),('帮助',[('已适配网站',window.show_supported),('日志与故障反馈',window.show_diagnostics)])]:
            button=QPushButton(title);button.setObjectName('chromeMenu');menu=QMenu(button)
            for text,callback in actions:menu.addAction(text,callback)
            button.setMenu(menu);row.addWidget(button)
        row.addStretch()
        for kind,tip,callback in [('minimize','最小化',window.showMinimized),('maximize','最大化 / 还原',self.toggle),('close','关闭',window.close)]:
            b=QPushButton();b.setIcon(control_icon(kind));b.setObjectName('windowControl');b.setAccessibleName(tip);b.setToolTip(tip);b.setFixedSize(33,28);b.clicked.connect(callback);row.addWidget(b)
    def toggle(self):
        self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()
    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.toggle()
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.window.windowHandle():self.window.windowHandle().startSystemMove()

class EdgeResize(QObject):
    def __init__(self,window):
        super().__init__(window);self.window=window;QApplication.instance().installEventFilter(self)
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton and not self.window.isMaximized() and hasattr(obj,'window') and obj.window() is self.window:
            p=self.window.mapFromGlobal(event.globalPosition().toPoint());r=self.window.rect();edges=Qt.Edge(0)
            if p.x()<6:edges|=Qt.Edge.LeftEdge
            if p.x()>r.width()-6:edges|=Qt.Edge.RightEdge
            if p.y()<6:edges|=Qt.Edge.TopEdge
            if p.y()>r.height()-6:edges|=Qt.Edge.BottomEdge
            if edges and self.window.windowHandle():return self.window.windowHandle().startSystemResize(edges)
        return False

class CoverDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        painter.save();r=option.rect.adjusted(5,5,-9,-9);cover=r.adjusted(0,0,0,-28)
        painter.fillRect(cover.translated(3,4),QColor('#181c22'))
        icon=index.data(Qt.ItemDataRole.DecorationRole)
        if icon:
            pix=icon.pixmap(QSize(400,600));scaled=pix.scaled(cover.size(),Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation)
            painter.setClipRect(cover);painter.drawPixmap(cover.x()+(cover.width()-scaled.width())//2,cover.y()+(cover.height()-scaled.height())//2,scaled);painter.setClipping(False)
        grad=QLinearGradient(0,cover.bottom()-52,0,cover.bottom());grad.setColorAt(0,QColor(0,0,0,0));grad.setColorAt(1,QColor(0,0,0,180));painter.fillRect(cover.adjusted(0,cover.height()-52,0,0),grad)
        text=index.data(Qt.ItemDataRole.DisplayRole).split('\n');painter.setPen(QColor('#d5d8dd'));font=QFont('Microsoft YaHei UI',9);painter.setFont(font)
        painter.drawText(cover.adjusted(8,0,-5,-8),Qt.AlignmentFlag.AlignBottom, text[-1] if len(text)>1 else '')
        painter.drawText(r.adjusted(0,r.height()-25,0,0),Qt.AlignmentFlag.AlignVCenter,QFontMetrics(font).elidedText(text[0],Qt.TextElideMode.ElideRight,r.width()))
        if option.state & (QStyle.StateFlag.State_MouseOver|QStyle.StateFlag.State_Selected):
            painter.setPen(QColor('#70bdf1'));painter.drawRect(cover.adjusted(0,0,-1,-1))
        painter.restore()
