import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class DesktopUI(unittest.TestCase):
    def test_tray_settings_floating_and_exit_lifecycle(self):
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,__file__,'--check'],env=dict(os.environ,LOCALAPPDATA=folder,QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1'),capture_output=True,text=True,encoding='utf-8',timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def run_mode(self,mode):
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,__file__,mode],env=dict(os.environ,LOCALAPPDATA=folder,QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1'),capture_output=True,text=True,encoding='utf-8',timeout=20)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_hidden_download_continues_and_exit_preserves_partial(self):self.run_mode('--download')
    def test_update_bypasses_remembered_hide(self):self.run_mode('--update')


def check():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import time,threading
    from argparse import Namespace
    from unittest.mock import patch
    from PySide6.QtCore import QTimer,QPoint,QPointF,QEvent,Qt,QRect
    from PySide6.QtGui import QEnterEvent,QMouseEvent
    from PySide6.QtWidgets import QApplication,QDialog,QSystemTrayIcon,QMessageBox
    import main,preferences
    from desktop_ui import CloseChoice,SettingsDialog,clamp
    def drive():
        app=QApplication.instance();window=next(w for w in app.topLevelWidgets() if type(w).__name__=='Window');desktop=window.desktop
        def settle(seconds=.05):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:app.processEvents();time.sleep(.005)
        assert not preferences.load()['float_enabled'] and desktop.panel is None
        assert preferences.load()['close_action']=='ask'
        def settings():
            dialog=app.activeModalWidget();assert isinstance(dialog,SettingsDialog)
            dialog.count.setValue(3);dialog.enabled.setChecked(True);dialog.opacity.setValue(70)
            if os.environ.get('DESKTOP_SCREENSHOT'):dialog.grab().save(str(Path(os.environ['DESKTOP_SCREENSHOT']).with_stem('settings-080-native')))
            dialog.apply()
        QTimer.singleShot(30,settings);window.show_settings();settle()
        panel=desktop.panel;assert panel and panel.isVisible() and panel.windowOpacity()>.65
        assert preferences.parallel_limit()==3 and panel.hide_timer.interval()==1000 and panel.rotate_timer.interval()==3000
        dialog=SettingsDialog(desktop);dialog.show();desktop.set_float_enabled(False);settle()
        assert not dialog.enabled.isChecked() and desktop.panel is None
        dialog.close();desktop.set_float_enabled(True);panel=desktop.panel;settle()
        queue=window.queue;queue.batch_rows={'a':{'id':'a','title':'video A','state':'running','total_bytes':100,'download_percent':50},'b':{'id':'b','title':'queued B','state':'queued'}}
        queue.active['a']=(None,None,threading.Event());queue.waiting.append(('b',[]))
        queue.snapshots['a']={'phase':'download','speed':2*1024**2,'_speed_at':time.monotonic()}
        desktop.refresh();assert '2.0 MiB/s' in window.total_speed_label.text()
        panel.rotate();assert panel.page_id=='a' and panel.title.text()=='video A'
        panel.rotate();assert panel.page_id is None # queued B never gets a page.
        queue.on_progress('a',{'phase':None,'stage':'校验视频完整性'});desktop.refresh()
        assert '0 B/s' in window.total_speed_label.text() and not panel.stats['transfers']
        area=panel.area();panel.expanded_pos=QPoint(area.left(),area.top()+100);panel.edge='left';panel.ensure_visible()
        with patch.object(panel,'underMouse',return_value=False):
            QApplication.sendEvent(panel,QEvent(QEvent.Type.Leave));assert panel.hide_timer.isActive()
            panel.hide_timer.setInterval(30);settle(.08)
            assert panel.collapsed and panel.width()==24 and panel.x()==area.left()
        screenshot=os.environ.get('DESKTOP_SCREENSHOT')
        if screenshot:panel.grab().save(str(Path(screenshot).with_stem(Path(screenshot).stem+'-tab')))
        QApplication.sendEvent(panel,QEnterEvent(QPointF(2,5),QPointF(2,5),QPointF(panel.mapToGlobal(QPoint(2,5)))))
        assert not panel.collapsed and panel.width()==320
        # Drag from the left edge to the right edge using actual mouse events.
        def mouse(kind,local,global_point,button,buttons):
            QApplication.sendEvent(panel,QMouseEvent(kind,QPointF(local),QPointF(global_point),button,buttons,Qt.KeyboardModifier.NoModifier))
        offset=QPoint(20,20);origin=panel.mapToGlobal(offset)
        mouse(QEvent.Type.MouseButtonPress,offset,origin,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
        target=QPoint(area.right()-panel.width()+1,area.top()+150)+offset
        mouse(QEvent.Type.MouseMove,offset,target,Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease,offset,target,Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton)
        assert panel.edge=='right' and panel.x()+panel.width()-1==area.right()
        assert preferences.load()['float_edge']=='right'
        # Drag off the dock; geometry and saved edge both become ordinary floating state.
        origin=panel.mapToGlobal(offset);mouse(QEvent.Type.MouseButtonPress,offset,origin,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
        target=QPoint(area.left()+150,area.top()+150)+offset
        mouse(QEvent.Type.MouseMove,offset,target,Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease,offset,target,Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton)
        assert panel.edge=='' and preferences.load()['float_edge']==''
        panel.expanded_pos=QPoint(99000,99000);panel.ensure_visible();assert area.contains(panel.geometry())
        assert clamp(QRect(-5000,-5000,320,124),QRect(-1920,0,1920,1080))==QPoint(-1920,0)
        desktop.save(float_auto_hide=False);assert not panel.collapsed
        if screenshot:panel.grab().save(screenshot)
        # Cancel does not remember an action or interrupt jobs.
        def choice(action,remember):
            def execute(dialog):dialog.action=action;dialog.remember.setChecked(remember);return QDialog.DialogCode.Accepted
            return execute
        with patch.object(CloseChoice,'exec',new=choice('cancel',True)),patch.object(queue,'shutdown') as shutdown:
            window.close();assert window.isVisible();shutdown.assert_not_called()
        with patch.object(CloseChoice,'exec',new=choice('hide',True)),patch.object(QSystemTrayIcon,'isSystemTrayAvailable',return_value=True),patch.object(desktop.tray,'show'),patch.object(desktop.tray,'isVisible',return_value=True),patch.object(queue,'shutdown') as shutdown:
            window.close();assert not window.isVisible() and desktop.hidden;shutdown.assert_not_called()
            assert preferences.load()['close_action']=='hide'
            desktop.restore_main();assert window.isVisible()
        with patch.object(QSystemTrayIcon,'isSystemTrayAvailable',return_value=False),patch.object(QMessageBox,'information') as info:
            window.close();assert window.isVisible() and not desktop.hidden;assert info.called
            desktop.hidden=True;window.hide();desktop.refresh();assert window.isVisible() and not desktop.hidden
        queue.active.clear();queue.waiting.clear();queue.batch_rows.clear()
        with patch.object(desktop,'allow_close',side_effect=AssertionError('explicit exit must bypass saved hide')):
            window.request_exit()
        assert desktop.stopped and not desktop.tray.isVisible() and desktop.panel is None
        assert preferences.load()['float_enabled'] # actual exit must preserve the switch.
        return 0
    with patch.object(QSystemTrayIcon,'isSystemTrayAvailable',return_value=False),patch.object(QApplication,'exec',side_effect=drive):
        assert main.gui(Namespace(smoke_ui=False,url=None))==0


def check_download():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import time
    from argparse import Namespace
    from unittest.mock import patch
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication,QSystemTrayIcon
    import main,engine,download_tasks as tasks
    root=Path(os.environ['LOCALAPPDATA']);registry=root/'tasks';registry.mkdir()
    made=[];state={'hidden_ticks':0,'hidden':False,'window':None}
    original=QApplication.exec
    def perform(info,format_id,output,cookies,progress,log,cancel,work):
        work.mkdir(parents=True,exist_ok=True);(work/'source.part').write_bytes(b'keep partial bytes')
        count=0
        while True:
            engine._check_cancel(cancel);count+=1
            engine._progress(progress,count,'下载中',speed=1024,download_percent=min(90,count),phase='download',downloaded_bytes=count,total_bytes=100)
            time.sleep(.03)
    def run():
        app=QApplication.instance();window=next(w for w in app.topLevelWidgets() if type(w).__name__=='Window');state['window']=window
        def received(task_id,data):
            if state['hidden']:
                state['hidden_ticks']+=1
                if state['hidden_ticks']==3:window.request_exit()
            else:
                assert window.desktop.hide_main();state['hidden']=True
                assert not window.isVisible() and window.queue.active
        window.queue.progress.connect(received)
        def enqueue():
            info={'url':'https://example.com/one','site':'test','title':'background','formats':[{'id':'source'}]}
            task=tasks.create(info,'source',str(root/'out'));made.append(task);window.queue.enqueue(task['id'],[],info)
        QTimer.singleShot(20,enqueue);QTimer.singleShot(6000,window.request_exit)
        with patch.object(window.desktop.tray,'show'),patch.object(window.desktop.tray,'isVisible',return_value=True):return original()
    with patch('download_tasks.directory',return_value=registry),patch('engine._perform_download',side_effect=perform),patch.object(QSystemTrayIcon,'isSystemTrayAvailable',return_value=True),patch.object(QApplication,'exec',side_effect=run):
        assert main.gui(Namespace(smoke_ui=False,url=None))==0
        assert state['hidden_ticks']>=3 and state['window'].desktop.stopped
        assert tasks.load(made[0]['id'])['state']=='paused'
        assert (tasks.work_path(made[0])/'source.part').read_bytes()==b'keep partial bytes'


def check_update():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from argparse import Namespace
    from unittest.mock import patch
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication,QSystemTrayIcon
    import main,updater,preferences
    from desktop_ui import CloseChoice
    original=QApplication.exec;windows=[];timers=[]
    def run():
        app=QApplication.instance();window=next(w for w in app.topLevelWidgets() if type(w).__name__=='Window');windows.append(window)
        window.desktop.save(close_action='hide')
        def click():
            dialog=next((w for w in app.topLevelWidgets() if type(w).__name__=='UpdateCenter' and w.isVisible()),None)
            if dialog and hasattr(dialog,'install') and dialog.install.isEnabled():dialog.install.click()
        timer=QTimer();timer.setInterval(20);timer.timeout.connect(click);timer.start();timers.append(timer)
        QTimer.singleShot(20,window.check_updates);QTimer.singleShot(5000,app.quit)
        return original()
    with patch.object(QSystemTrayIcon,'isSystemTrayAvailable',return_value=False),patch.object(QApplication,'exec',side_effect=run),patch.object(CloseChoice,'exec',side_effect=AssertionError('update must not ask to hide')),patch('updater.check_update',return_value={'available':True,'version':'99.0.0'}),patch('updater.prepare_update',return_value={'plan':'fixture','helper':'fixture'}),patch('updater.launch_update') as launch:
        assert main.gui(Namespace(smoke_ui=False,url=None))==0
        launch.assert_called_once();assert windows[0].desktop.stopped and windows[0].force_exit
        assert preferences.load()['close_action']=='hide'


if __name__=='__main__':
    if '--check' in sys.argv:check()
    if '--download' in sys.argv:check_download()
    if '--update' in sys.argv:check_update()
