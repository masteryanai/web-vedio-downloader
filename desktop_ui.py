"""Tray lifecycle, explicit settings and a recoverable edge-docking monitor."""
from PySide6.QtCore import Qt,QObject,QTimer,QPoint,QRect,Signal
from PySide6.QtGui import QGuiApplication,QIcon,QPixmap,QPainter,QColor,QPen
from PySide6.QtWidgets import (QApplication,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QCheckBox,QSpinBox,QComboBox,QFrame,QProgressBar,QMenu,QSystemTrayIcon,QMessageBox)
import preferences
import diagnostics
from transfer_stats import speed_text,overview


def clamp(rect,area):
    return QPoint(max(area.left(),min(rect.x(),area.right()-rect.width()+1)),
                  max(area.top(),min(rect.y(),area.bottom()-rect.height()+1)))


def app_icon():
    pixmap=QPixmap(32,32);pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#285ed4'));painter.drawRoundedRect(1,1,30,30,7,7)
    painter.setPen(QPen(QColor('white'),3));painter.drawLine(16,7,16,22)
    painter.drawLine(9,16,16,23);painter.drawLine(16,23,23,16);painter.end()
    return QIcon(pixmap)


class CloseChoice(QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.action='cancel';self.setWindowTitle('关闭窗口');self.resize(460,210)
        layout=QVBoxLayout(self)
        label=QLabel('隐藏到托盘后继续运行。\n退出会暂停任务并保留续传进度。');layout.addWidget(label)
        self.remember=QCheckBox('记住选择（可在设置中修改）');layout.addWidget(self.remember)
        row=QHBoxLayout()
        for action,text in (('hide','隐藏到托盘'),('exit','退出'),('cancel','取消')):
            button=QPushButton(text);button.clicked.connect(lambda _,key=action:self.choose(key));row.addWidget(button)
        layout.addLayout(row)
    def choose(self,action):self.action=action;self.accept()


class SettingsDialog(QDialog):
    def __init__(self,desktop):
        super().__init__(desktop.window);self.desktop=desktop;self.setWindowTitle('设置');self.resize(440,390)
        cfg=preferences.load();layout=QVBoxLayout(self)
        layout.addWidget(QLabel('同时运行的下载任务数（1～10）'))
        self.count=QSpinBox();self.count.setRange(1,10);self.count.setValue(desktop.queue.limit);layout.addWidget(self.count)
        layout.addWidget(QLabel('点击主窗口关闭按钮时'))
        self.close_action=QComboBox()
        for text,key in (('每次询问','ask'),('隐藏到托盘','hide'),('退出程序','exit')):self.close_action.addItem(text,key)
        self.close_action.setCurrentIndex(self.close_action.findData(cfg['close_action']));layout.addWidget(self.close_action)
        self.enabled=QCheckBox('开启悬浮框');self.enabled.setChecked(cfg['float_enabled']);layout.addWidget(self.enabled)
        self.auto_hide=QCheckBox('靠近左右边缘时自动收起');self.auto_hide.setChecked(cfg['float_auto_hide']);layout.addWidget(self.auto_hide)
        row=QHBoxLayout();row.addWidget(QLabel('悬浮框不透明度'))
        self.opacity=QSpinBox();self.opacity.setRange(20,100);self.opacity.setSuffix('%');self.opacity.setValue(cfg['float_opacity']);row.addWidget(self.opacity);layout.addLayout(row)
        note=QLabel('悬浮框每 3 秒轮播概况与正在传输的任务。\n降低并行上限不会中断正在运行的任务。');note.setWordWrap(True);layout.addWidget(note)
        self.error=QLabel();self.error.setWordWrap(True);layout.addWidget(self.error)
        save=QPushButton('保存');save.clicked.connect(self.apply);layout.addWidget(save)
        desktop.preferences_changed.connect(self.sync)
    def sync(self,changes):
        if 'float_enabled' in changes:self.enabled.setChecked(changes['float_enabled'])
        if 'float_auto_hide' in changes:self.auto_hide.setChecked(changes['float_auto_hide'])
    def apply(self):
        try:
            self.desktop.save(parallel_downloads=self.count.value(),close_action=self.close_action.currentData(),
                float_enabled=self.enabled.isChecked(),float_auto_hide=self.auto_hide.isChecked(),float_opacity=self.opacity.value())
            self.accept()
        except Exception as error:self.error.setText('设置未保存：'+diagnostics.redact(str(error)))


class FloatingPanel(QFrame):
    def __init__(self,desktop,cfg):
        super().__init__(None,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
        self.desktop=desktop;self.cfg=cfg;self.edge=cfg['float_edge'];self.collapsed=False;self.shutting_down=False
        self.pressed=False;self.dragging=False;self.menu_open=False;self.page_id=None;self.stats=None
        self.setObjectName('floatingMonitor');self.setStyleSheet('#floatingMonitor {background:#f5f8ff;border:1px solid #8ca9df;border-radius:7px;}')
        self.setWindowTitle('下载悬浮框');self.setMouseTracking(True);self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.layout_box=QVBoxLayout(self);self.layout_box.setContentsMargins(10,8,10,8)
        self.content=QFrame();box=QVBoxLayout(self.content);box.setContentsMargins(0,0,0,0);box.setSpacing(4)
        self.title=QLabel('本轮下载');self.detail=QLabel();self.detail.setWordWrap(True)
        self.bar=QProgressBar();self.bar.setFixedHeight(20)
        for widget in (self.title,self.bar,self.detail):box.addWidget(widget)
        self.tab=QLabel('下\n载');self.tab.setAlignment(Qt.AlignmentFlag.AlignCenter);self.tab.hide()
        self.layout_box.addWidget(self.content);self.layout_box.addWidget(self.tab)
        for child in self.findChildren(QFrame)+self.findChildren(QLabel)+self.findChildren(QProgressBar):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(320,124)
        area=QGuiApplication.primaryScreen().availableGeometry()
        position=cfg['float_position'];self.expanded_pos=QPoint(*position) if position else QPoint(area.right()-self.width()-24,area.bottom()-self.height()-24)
        self.move(self.expanded_pos)
        self.hide_timer=QTimer(self);self.hide_timer.setSingleShot(True);self.hide_timer.setInterval(1000);self.hide_timer.timeout.connect(self.collapse)
        self.rotate_timer=QTimer(self);self.rotate_timer.setInterval(3000);self.rotate_timer.timeout.connect(self.rotate);self.rotate_timer.start()
        app=QGuiApplication.instance();app.screenAdded.connect(self.screen_added);app.screenRemoved.connect(self.screen_changed)
        for screen in app.screens():self.watch_screen(screen)
        self.configure(cfg);self.ensure_visible();self.show()
        if self.edge and cfg['float_auto_hide']:self.hide_timer.start()

    def watch_screen(self,screen):
        screen.availableGeometryChanged.connect(self.screen_changed);screen.geometryChanged.connect(self.screen_changed)
    def screen_added(self,screen):self.watch_screen(screen);self.ensure_visible()
    def screen_changed(self,*_):self.ensure_visible()
    def area(self):
        screen=QGuiApplication.screenAt(QRect(self.expanded_pos,self.expanded_size()).center()) or QGuiApplication.primaryScreen()
        return screen.availableGeometry()
    def expanded_size(self):
        from PySide6.QtCore import QSize
        return QSize(320,124)
    def ensure_visible(self):
        if self.pressed or self.shutting_down:return
        area=self.area();self.expanded_pos=clamp(QRect(self.expanded_pos,self.expanded_size()),area)
        if self.edge=='left':self.expanded_pos.setX(area.left())
        if self.edge=='right':self.expanded_pos.setX(area.right()-319)
        if self.collapsed:self.move(area.left() if self.edge=='left' else area.right()-23,self.expanded_pos.y())
        else:self.move(self.expanded_pos)
    def configure(self,cfg):
        self.cfg=cfg;self.setWindowOpacity(cfg['float_opacity']/100)
        if not cfg['float_auto_hide']:self.hide_timer.stop();self.expand()
        elif self.edge and not self.underMouse():self.hide_timer.start()
    def update_stats(self,stats):
        self.stats=stats
        if self.page_id not in [row['id'] for row in stats['transfers']]:self.page_id=None
        self.render()
    def rotate(self):
        if not self.stats or self.menu_open or self.pressed:return
        ids=[None]+[row['id'] for row in self.stats['transfers']]
        self.page_id=ids[(ids.index(self.page_id)+1)%len(ids)] if self.page_id in ids else None;self.render()
    def render(self):
        if not self.stats:return
        row=next((row for row in self.stats['transfers'] if row['id']==self.page_id),None)
        if row:
            title=row['title'];percent=row.get('download_percent');detail='下载速度 '+speed_text(row['live_speed'])
        else:
            title='本轮下载概况';percent=self.stats['percent'];detail=f"总速度 {speed_text(self.stats['speed'])}\n活动 {self.stats['active']} · 排队 {self.stats['queued']} · 解析/处理 {self.stats['processing']}\n暂停/待处理 {self.stats['paused']}"
        from PySide6.QtGui import QFontMetrics
        self.title.setText(QFontMetrics(self.title.font()).elidedText(title,Qt.TextElideMode.ElideRight,292))
        self.title.setToolTip(title);self.detail.setText(detail);self.setToolTip(title+'\n'+detail)
        self.bar.setRange(0,100);self.bar.setValue(int(percent or 0));self.bar.setFormat(f'{percent:.1f}%' if percent is not None else '暂无任务' if not self.stats['count'] else '总量未知')
    def collapse(self):
        if self.shutting_down or not self.edge or not self.cfg['float_auto_hide'] or self.pressed or self.menu_open or self.underMouse():return
        self.expanded_pos=self.pos();self.collapsed=True;self.content.hide();self.tab.show()
        self.layout_box.setContentsMargins(1,2,1,2);self.setFixedSize(24,80);self.ensure_visible()
    def expand(self):
        if not self.collapsed:return
        self.collapsed=False;self.tab.hide();self.content.show();self.layout_box.setContentsMargins(10,8,10,8)
        self.setFixedSize(self.expanded_size());self.ensure_visible()
    def enterEvent(self,event):self.hide_timer.stop();self.expand();super().enterEvent(event)
    def leaveEvent(self,event):
        if self.edge and self.cfg['float_auto_hide'] and not self.pressed:self.hide_timer.start()
        super().leaveEvent(event)
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.hide_timer.stop();self.expand();self.pressed=True;self.dragging=False
            self.press_point=event.globalPosition().toPoint();self.drag_offset=self.press_point-self.pos();event.accept()
        else:super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self.pressed:
            point=event.globalPosition().toPoint()
            if (point-self.press_point).manhattanLength()>=QApplication.startDragDistance():self.dragging=True;self.edge=''
            if self.dragging:self.move(point-self.drag_offset)
            event.accept()
        else:super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.pressed:
            self.pressed=False
            if self.dragging:
                self.expanded_pos=self.pos();self.ensure_visible();area=self.area()
                if abs(self.x()-area.left())<=14:self.edge='left';self.expanded_pos.setX(area.left())
                elif abs(self.x()+self.width()-1-area.right())<=14:self.edge='right';self.expanded_pos.setX(area.right()-self.width()+1)
                self.move(self.expanded_pos);self.desktop.save_position(self.expanded_pos,self.edge)
            self.dragging=False;event.accept()
        else:super().mouseReleaseEvent(event)
    def mouseDoubleClickEvent(self,event):
        self.pressed=False;self.dragging=False;self.desktop.restore_main();event.accept()
    def contextMenuEvent(self,event):
        self.menu_open=True;self.hide_timer.stop();menu=QMenu(self)
        restore=menu.addAction('打开主界面');tasks=menu.addAction('下载任务');settings=menu.addAction('设置')
        menu.addSeparator();auto=menu.addAction('靠边自动收起');auto.setCheckable(True);auto.setChecked(self.cfg['float_auto_hide'])
        close=menu.addAction('关闭悬浮框');selected=menu.exec(event.globalPos());self.menu_open=False
        if selected==close:self.desktop.set_float_enabled(False);return
        if selected==restore:self.desktop.restore_main()
        elif selected==tasks:self.desktop.open_tasks()
        elif selected==settings:self.desktop.window.show_settings()
        elif selected==auto:self.desktop.save(float_auto_hide=auto.isChecked())
        if self.edge and not self.underMouse():self.hide_timer.start()
    def closeEvent(self,event):
        if self.shutting_down:event.accept()
        else:event.ignore();self.desktop.set_float_enabled(False)
    def shutdown(self):
        self.shutting_down=True;self.hide_timer.stop();self.rotate_timer.stop();self.hide();self.deleteLater()


class DesktopController(QObject):
    preferences_changed=Signal(object)
    def __init__(self,window,queue):
        super().__init__(window);self.window=window;self.queue=queue;self.cfg=preferences.load()
        self.panel=None;self.settings=None;self.hidden=False;self.hidden_windows=[];self.stopped=False;self.was_maximized=False
        self.tray=QSystemTrayIcon(app_icon(),self);window.setWindowIcon(self.tray.icon());self.tray.setToolTip('Web Video Downloader')
        menu=QMenu(window);menu.addAction('显示主界面',self.restore_main);menu.addAction('下载任务',self.open_tasks)
        self.float_action=menu.addAction('显示悬浮框');self.float_action.setCheckable(True);self.float_action.triggered.connect(self.set_float_enabled)
        menu.addSeparator();menu.addAction('退出',window.request_exit);self.tray.setContextMenu(menu);self.tray_menu=menu
        self.tray.activated.connect(self.activated)
        if QSystemTrayIcon.isSystemTrayAvailable():self.tray.show()
        self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.refresh);self.timer.start()
        self.apply(self.cfg);self.refresh()
    def activated(self,reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,QSystemTrayIcon.ActivationReason.DoubleClick):self.restore_main()
    def open_tasks(self):self.restore_main();self.window.show_tasks()
    def restore_main(self):
        maximize=self.window.isMaximized() or (self.hidden and self.was_maximized)
        self.hidden=False;self.was_maximized=False
        if maximize:self.window.showMaximized()
        else:self.window.showNormal()
        self.window.raise_();self.window.activateWindow()
        for widget in self.hidden_windows:
            try:widget.show()
            except RuntimeError:pass
        self.hidden_windows=[]
    def hide_main(self):
        if not QSystemTrayIcon.isSystemTrayAvailable() or self.tray.icon().isNull():
            QMessageBox.information(self.window,'无法隐藏到托盘','当前托盘不可用，主窗口将保留。你仍可从任务栏最小化，或在设置中选择退出。');return False
        self.tray.show()
        if not self.tray.isVisible():return False
        self.hidden_windows=[]
        for name in ('task_dialog','accounts_dialog','diagnostic_dialog','browser_dialog','resume_browser'):
            widget=getattr(self.window,name,None)
            if widget and widget.isVisible():self.hidden_windows.append(widget);widget.hide()
        self.was_maximized=self.window.isMaximized();self.hidden=True;self.window.hide();diagnostics.event('desktop.hidden');return True
    def allow_close(self):
        action=self.cfg['close_action'];remember=False
        if action=='ask':
            choice=CloseChoice(self.window);choice.exec();action=choice.action;remember=choice.remember.isChecked();choice.deleteLater()
        if action=='cancel':return False
        if action=='hide' and not self.hide_main():return False
        if remember:
            try:self.save(close_action=action)
            except Exception as error:QMessageBox.warning(self.window,'关闭选项未保存',diagnostics.redact(str(error)))
        return action=='exit'
    def show_settings(self):
        if self.settings:self.settings.show();self.settings.raise_();return
        dialog=SettingsDialog(self);self.settings=dialog
        try:dialog.exec()
        finally:self.settings=None;dialog.deleteLater()
    def save(self,**changes):
        cfg=preferences.update(**changes)
        if 'parallel_downloads' in changes:self.queue.set_limit(cfg['parallel_downloads'])
        self.apply(cfg);self.preferences_changed.emit(changes)
    def save_position(self,point,edge):
        try:self.cfg=preferences.update(float_position=[point.x(),point.y()],float_edge=edge)
        except Exception as error:diagnostics.exception('floating.position.failed',error,error.__traceback__)
    def set_float_enabled(self,enabled):
        try:self.save(float_enabled=bool(enabled))
        except Exception as error:QMessageBox.warning(self.window,'设置未保存',diagnostics.redact(str(error)))
    def apply(self,cfg):
        self.cfg=cfg;self.float_action.setChecked(cfg['float_enabled'])
        if cfg['float_enabled']:
            if self.panel:self.panel.configure(cfg)
            else:self.panel=FloatingPanel(self,cfg)
        elif self.panel:self.panel.shutdown();self.panel=None
    def refresh(self):
        if self.stopped:return
        stats=self.queue.statistics()
        self.window.total_speed_label.setText('整体下载速度：'+speed_text(stats['speed']))
        self.window.total_speed_label.setToolTip(overview(stats));self.tray.setToolTip('Web Video Downloader\n总速度 '+speed_text(stats['speed']))
        if self.panel:self.panel.update_stats(stats)
        if self.hidden and not QSystemTrayIcon.isSystemTrayAvailable():
            self.restore_main();QMessageBox.information(self.window,'托盘暂不可用','已恢复主窗口，避免隐藏后无法找回。')
    def shutdown(self):
        self.stopped=True;self.timer.stop();self.tray.hide()
        if self.settings:self.settings.reject()
        if self.panel:self.panel.shutdown();self.panel=None
