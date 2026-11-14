"""Run widget checks in a separate process so QCoreApplication tests stay isolated."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class TaskViewTest(unittest.TestCase):
    def test_task_window_filters_layout_and_id_actions(self):
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1')
        result=subprocess.run([sys.executable,'-c',
            "import runpy;runpy.run_path('tests/test_task_view.py',run_name='__main__')",'--gui-check'],
            capture_output=True,text=True,encoding='utf-8',env=env,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)


def gui_check():
    import json,time
    from PySide6.QtWidgets import QApplication,QMessageBox,QStyleOptionViewItem,QProgressBar,QDialog
    from PySide6.QtCore import Qt,QTimer,QRect
    import download_tasks as tasks
    from download_queue import DownloadQueue
    from task_view import TaskDialog,DeleteDialog,STATUS_TEXTS
    from PySide6.QtGui import QFont,QFontMetrics,QImage,QPainter,QColor
    from main import STYLE
    app=QApplication([]);app.setFont(QFont('Microsoft YaHei UI',10));app.setStyleSheet(STYLE)
    def settle():
        for _ in range(4):app.processEvents();time.sleep(.01)
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);registry=root/'tasks';registry.mkdir()
        with patch('download_tasks.directory',return_value=registry):
            created=[];outputs=[]
            for name,state in [('长标题测试'*30+'\n第二行','paused'),('pending-failed','failed'),('queued','queued'),('checking-100','running'),('completed-one','completed'),('completed-two','completed'),('cancelled','cancelled')]:
                info={'url':'https://example.com/'+str(len(created)),'site':'AGE动漫','title':name,'formats':[{'id':'source'}]}
                output=str(root/'short') if created else str(root/('保存位置'*20)/('长路径'*20))
                task=tasks.create(info,'source',output);task['state']=state;task['download_percent']=100 if state in ('running','completed') else 42
                if state=='completed':
                    video=root/(name+'.mp4');video.write_bytes(b'user file');outputs.append(video)
                    task['result']={'path':str(video),'verified':True};tasks.save(task);tasks.finish(task)
                elif state=='cancelled':
                    task['state']='paused';tasks.save(task);tasks.discard(task['id'])
                else:tasks.save(task)
                created.append(task)
            tasks.history_file('d'*32).write_text('{broken')
            queue=DownloadQueue();dialog=TaskDialog(queue,lambda _:None);dialog.show();settle()
            assert dialog.table.rowCount()==7
            assert dialog.table.itemDelegateForColumn(3) is not None, '状态列缺少防溢出绘制'
            cell=dialog.table.cellWidget(0,1);bar=cell.findChild(QProgressBar)
            assert abs(bar.y()-(cell.height()-bar.y()-bar.height()))<=1
            assert bar.alignment()==Qt.AlignmentFlag.AlignCenter
            assert not dialog.table.wordWrap() and dialog.table.rowHeight(0)==32
            option=QStyleOptionViewItem()
            dialog.table.itemDelegateForColumn(0).initStyleOption(option,dialog.table.model().index(0,0))
            assert option.textElideMode==Qt.TextElideMode.ElideRight
            assert '\n' not in dialog.table.item(0,0).text()
            assert dialog.table.item(0,0).toolTip()==created[0]['title']
            assert dialog.table.horizontalScrollBar().maximum()>0
            # Real running status uses a short label but retains its full source message.
            running_id=created[3]['id'];raw='AGE：连接视频源，确认已下载进度。'
            queue.active[running_id]=None;queue.snapshots[running_id]={'stage':raw}
            dialog.refresh();settle()
            status=dialog.table.item(3,3)
            assert status.text()=='确认已下载进度' and status.toolTip()==raw
            delegate=dialog.table.itemDelegateForColumn(3)
            default_font=QFont(dialog.table.font())
            for points in (9,14):
                font=QFont(default_font);font.setPointSize(points);dialog.table.setFont(font);settle()
                metrics=QFontMetrics(dialog.table.font())
                assert all(metrics.horizontalAdvance(text)+12<=dialog.table.columnWidth(3) for text in STATUS_TEXTS)
            dialog.table.setFont(default_font);settle()
            baseline=dialog.table.columnWidth(3)
            unknown='未知超长状态'*80;queue.snapshots[running_id]={'stage':unknown};dialog.refresh();settle()
            assert dialog.table.columnWidth(3)==baseline # runtime messages do not resize the column.
            assert dialog.table.item(3,3).toolTip()==unknown
            option=QStyleOptionViewItem();option.initFrom(dialog.table)
            delegate.initStyleOption(option,dialog.table.model().index(3,3))
            assert option.textElideMode==Qt.TextElideMode.ElideRight
            option.rect=QRect(0,0,90,32);option.widget=dialog.table
            canvas=QImage(240,32,QImage.Format.Format_ARGB32);sentinel=QColor('magenta');canvas.fill(sentinel)
            painter=QPainter(canvas);delegate.paint(painter,option,dialog.table.model().index(3,3));painter.end()
            assert all(canvas.pixelColor(x,y)==sentinel for x in range(90,240) for y in range(32)), 'status paint escaped into the path cell'
            dialog.table.setColumnWidth(3,90);dialog.refresh();settle()
            assert dialog.table.columnWidth(3)==90
            dialog.resize(1200,520);settle();assert dialog.table.columnWidth(3)==90
            dialog.status_default_width=None;dialog.fit_columns();dialog.resize(1100,520);settle()
            queue.snapshots[running_id]={'stage':raw};dialog.refresh();settle()
            screenshot=os.environ.get('TASK_VIEW_SCREENSHOT')
            if screenshot:dialog.grab().save(screenshot)
            queue.active.pop(running_id);queue.snapshots.pop(running_id);dialog.refresh()
            dialog.tabs.setCurrentIndex(1);assert dialog.table.rowCount()==4
            assert dialog.buttons['delete'].isHidden()
            assert all(not dialog.buttons[key].isHidden() for key in ('pause','resume','cancel'))
            assert created[3]['id'] in dialog.visible # 100% while verifying is still ongoing.
            dialog.tabs.setCurrentIndex(2);assert dialog.table.rowCount()==2
            assert not dialog.buttons['clear'].isHidden() and not dialog.buttons['clear'].isEnabled()
            dialog.table.selectRow(0)
            with patch.object(DeleteDialog,'exec') as popup:
                dialog.delete();popup.assert_not_called()
            dialog.select_all.click();assert len(dialog.checked_ids)==2
            dialog.refresh();assert len(dialog.checked_ids)==2
            assert dialog.buttons['clear'].isEnabled()
            dialog.select_none.click();assert not dialog.checked_ids and not dialog.buttons['clear'].isEnabled()
            dialog.tabs.setCurrentIndex(3);assert dialog.table.rowCount()==1
            dialog.tabs.setCurrentIndex(0)
            dialog.search.setText('AGE动漫');assert dialog.table.rowCount()==7
            dialog.select_all.click();assert len(dialog.checked_ids)==7
            assert dialog.buttons['delete'].isHidden()
            with patch.object(queue,'pause') as pause:
                queue.waiting.append((created[2]['id'],[]))
                dialog.pause();assert pause.call_args.args==(created[2]['id'],)
                queue.waiting.clear()
            called=[]
            def resume(task_id):
                called.append(task_id)
                if task_id==created[1]['id']:raise ValueError('fixture failure')
                return True
            dialog.resume_task=resume;dialog.resume()
            assert set(called)=={row['id'] for row in created[:4]}
            assert '失败 1 条' in dialog.batch_result.text() and '跳过 3 条' in dialog.batch_result.text()
            dialog.search.setText('pending');assert not dialog.checked_ids
            dialog.search.setText('保存位置');assert dialog.table.rowCount()==1
            dialog.table.selectRow(0);dialog.table.item(0,0).setCheckState(Qt.CheckState.Checked)
            def change_filter_during_confirmation(*_):
                dialog.search.setText('pending-failed');dialog.refresh()
                return QMessageBox.StandardButton.Yes
            with patch.object(QMessageBox,'question',side_effect=change_filter_during_confirmation):dialog.cancel()
            assert tasks.load_record(created[0]['id'])['state']=='cancelled'
            assert tasks.load(created[1]['id'])['state']=='failed'
            dialog.search.setText('completed-one');settle();dialog.table.selectRow(0);dialog.table.item(0,0).setCheckState(Qt.CheckState.Checked)
            assert dialog.buttons['delete'].isEnabled() and not dialog.buttons['resume'].isEnabled()
            selected=dialog.selected()['id'];dialog.refresh()
            assert dialog.selected()['id']==selected and dialog.search.text()=='completed-one'
            before=dialog.table.columnWidth(0);dialog.resize(1800,600);settle()
            assert dialog.table.columnWidth(0)>before
            assert dialog.table.horizontalHeader().length()>=dialog.table.viewport().width()-1
            dialog.resize(2200,600);settle()
            assert abs(dialog.table.horizontalHeader().length()-dialog.table.viewport().width())<=1
            if screenshot:dialog.grab().save(str(Path(screenshot).with_stem(Path(screenshot).stem+'-wide')))
            def select_other(choice):
                assert choice.record_only.isChecked() and not choice.with_cache.isChecked()
                dialog.search.setText('completed-two');dialog.refresh();dialog.table.selectRow(0);dialog.table.item(0,0).setCheckState(Qt.CheckState.Checked)
                return QDialog.DialogCode.Accepted
            with patch.object(DeleteDialog,'exec',new=select_other):dialog.delete()
            assert not tasks.history_file(selected).exists()
            assert tasks.load_record(created[5]['id'])['state']=='completed'
            # Exercise actual modal cancellation and the second deletion choice.
            dialog.search.setText('completed-two');settle();dialog.table.selectRow(0);dialog.table.item(0,0).setCheckState(Qt.CheckState.Checked)
            cache=tasks.work_path(created[5]);cache.mkdir(parents=True);(cache/'source.part').write_bytes(b'cache')
            def close_choice():
                choice=app.activeModalWidget();assert isinstance(choice,DeleteDialog)
                assert choice.record_only.isChecked()
                if screenshot:choice.grab().save(str(Path(screenshot).with_stem(Path(screenshot).stem+'-delete')))
                choice.reject()
            QTimer.singleShot(40,close_choice);dialog.delete()
            assert tasks.history_file(created[5]['id']).exists() and cache.exists()
            # Keep this fixture for the restart check; use the cancelled row for clean-cache deletion.
            dialog.search.setText('cancelled');settle();dialog.table.selectRow(0);dialog.table.item(0,0).setCheckState(Qt.CheckState.Checked)
            cancelled_cache=tasks.work_path(created[6]);cancelled_cache.mkdir(parents=True)
            (cancelled_cache/'source.part').write_bytes(b'cache')
            def clean_choice():
                choice=app.activeModalWidget();assert isinstance(choice,DeleteDialog)
                choice.with_cache.setChecked(True);choice.accept()
            QTimer.singleShot(40,clean_choice);dialog.delete()
            assert not tasks.history_file(created[6]['id']).exists() and not cancelled_cache.exists()
            assert all(video.read_bytes()==b'user file' for video in outputs)
            # Re-read physical records in a fresh interpreter.
            code="import json,sys;from pathlib import Path;import download_tasks as t;t.directory=lambda:Path(sys.argv[1]);r=t.records();print(json.dumps([sum(x['state'] in ('queued','running','paused','failed') for x in r),sum(x['state']=='completed' for x in r),sum(x['state']=='cancelled' for x in r)]))"
            counts=json.loads(subprocess.check_output([sys.executable,'-c',code,str(registry)],text=True,encoding='utf-8'))
            assert counts==[3,1,1],counts
            # A batch clear only removes checked completed records, preserving unselected files.
            extras=[]
            for n in range(3):
                task=tasks.create({'url':f'https://example.com/bulk/{n}','site':'AGE动漫','title':f'bulk-{n}'},'source',str(root/'out'))
                task['state']='completed';tasks.save(task);tasks.finish(task);extras.append(task)
            dialog.search.clear();dialog.tabs.setCurrentIndex(2);dialog.refresh()
            for i in range(dialog.table.rowCount()):
                item=dialog.table.item(i,0)
                if item.data(Qt.ItemDataRole.UserRole) in {row['id'] for row in extras[:2]}:item.setCheckState(Qt.CheckState.Checked)
            assert len(dialog.checked_ids)==2
            with patch.object(DeleteDialog,'exec',return_value=QDialog.DialogCode.Accepted):dialog.buttons['clear'].click()
            assert all(not tasks.history_file(row['id']).exists() for row in extras[:2])
            assert tasks.history_file(extras[2]['id']).exists() and all(video.exists() for video in outputs)
            dialog.close();settle()
    print('Task dialog layout/search/grouping/ID actions/history restart: PASS')


if __name__=='__main__' and '--gui-check' in sys.argv:gui_check()
