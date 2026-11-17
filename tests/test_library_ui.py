import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class LibraryUI(unittest.TestCase):
    def test_batch_library_playback_scope_and_embedded_tasks(self):
        self.run_mode('--check')
    def test_large_collection_pagination_lazy_mapping_and_async_prepare(self):
        self.run_mode('--large')
    def test_cancel_batch_prepare_never_enqueues_late_results(self):
        self.run_mode('--cancel')
    def run_mode(self,mode):
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,WVD_DATA_DIR=str(Path(folder)/'data'),LOCALAPPDATA=str(Path(folder)/'legacy'),PYTHONUTF8='1',PYTHONFAULTHANDLER='1',QT_QPA_PLATFORM='offscreen')
            process=subprocess.run([sys.executable,__file__,mode],env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
            self.assertEqual(process.returncode,0,process.stdout+process.stderr)

def check():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import time,threading,traceback
    from argparse import Namespace
    from unittest.mock import patch
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication,QMessageBox
    import main,engine
    original=QApplication.exec;errors=[];done=[];observations=[]
    cancelled='--cancel' in sys.argv;large='--large' in sys.argv or cancelled;total=1001 if large else 2;selected_count=25 if large else 2
    root=Path(os.environ['WVD_DATA_DIR']).parent
    group={'site':'test','key':'season1','url':'https://example.com/1','title':'试验作品','season':'第一季','entries':[{'key':str(i),'title':f'第{i}集','url':f'https://example.com/{i}'} for i in range(1,total+1)]}
    def inspect(url,*args,**kwargs):return {'site':'test','title':'试验作品','url':url,'formats':[{'id':'f720','label':'720P','width':1280,'height':720}],'duration':20}
    def perform(info,format_id,output,*args):
        path=Path(output)/(info['title']+'.mp4');path.write_bytes(b'verified fixture');return {'path':str(path),'verified':True}
    def run():
        app=QApplication.instance();window=next(w for w in app.topLevelWidgets() if type(w).__name__=='Window');ws=window.workspace
        def drive():
            try:
                assert ws.pages.currentIndex()==0
                lib=ws.store.library(str(root/'films'));ws.open_add();window.info=inspect(group['entries'][0]['url']);window.info['_collection']=group;ws.inspected(window.info)
                assert ws.add_controls.table.rowCount()==total and ws.add_controls.season.text()=='第一季'
                if large:
                    from PySide6.QtWidgets import QComboBox
                    assert len(ws.add_controls.findChildren(QComboBox))<20,'mapping widgets were eagerly created for every episode'
                    from PySide6.QtCore import Qt
                    for i in range(selected_count,total):ws.add_controls.table.item(i,0).setCheckState(Qt.CheckState.Unchecked)
                if cancelled:
                    ready=threading.Event();released=threading.Event();bind=ws.store.bind_many
                    def delayed_bind(pairs):bind(pairs);ready.set();assert released.wait(5)
                    ws.store.bind_many=delayed_bind
                    def cancel_ready():
                        if ready.is_set():ws.cancel_batches();released.set()
                        else:QTimer.singleShot(5,cancel_ready)
                    QTimer.singleShot(5,cancel_ready)
                window.download()
                assert len(ws.store.read()['works'])==1 and len(ws.store.read()['episodes'])==total
                assert ws.pages.currentIndex()==1
                assert ws.add_dialog.isVisible() if large else not ws.add_dialog.isVisible()
                if large:
                    assert ws.episodes.rowCount()==50;ws.turn_page(1);assert ws.episode_page==1;ws.turn_page(-1)
            except BaseException as error:errors.append(traceback.format_exc());window.request_exit()
        def snapshot():
            rows=__import__('download_tasks').records()
            counts={}
            for row in rows:counts[row['state']]=counts.get(row['state'],0)+1
            return {'active':list(window.queue.active),'waiting':list(window.queue.waiting),'batch_preparing':ws.batch_preparing,'batch_worker':ws.batch_worker,'prepared_batch':len(ws.prepared_batch),'jobs':len(ws.jobs.running),'states':counts,'failed':[(r['title'],r.get('error'),r.get('stage')) for r in rows if r['state'] in ('failed','paused')],'errors':[str(e) for e in errors]}
        def verify():
            if errors:return
            current=snapshot()
            if not observations or observations[-1]!=current:
                observations.append(current)
                if len(observations)>16:observations.pop(0)
            if window.queue.active or window.queue.waiting or ws.batch_preparing or ws.batch_worker:QTimer.singleShot(50,verify);return
            try:
                if cancelled:
                    assert ws.add_dialog.isVisible()
                    assert not window.queue.completed_rows and not window.queue.active and not window.queue.waiting
                    records=__import__('download_tasks').records();assert len(records)==selected_count
                    assert all(r['state']=='paused' for r in records)
                    done.append(True);window.request_exit();return
                ws.store.sync(__import__('download_tasks').records());data=ws.store.read();work=data['works'][0]
                assert not ws.add_dialog.isVisible()
                downloaded=data['episodes'][:selected_count];assert all(e['files'] for e in downloaded),{'missing':[e['label'] for e in downloaded if not e['files']],'queue':snapshot(),'recent':observations}
                assert all(Path(e['files'][0]['path']).is_relative_to(root/'films') for e in downloaded)
                assert all('第一季' in e['files'][0]['path'] for e in downloaded)
                ws.states={e['id']:'已下载' for e in downloaded};ws.show_work(work['id']);ws.store.patch_work(work['id'],no_play_prompt=True)
                with patch('library_ui.QDesktopServices.openUrl',return_value=True) as opened:ws.episode_action(data['episodes'][0]['id']);opened.assert_called_once()
                ws.open_add();window.info=inspect(group['entries'][0]['url']);window.info['_collection']=group;ws.inspected(window.info)
                assert all(ws.add_controls.table.item(i,0).checkState().value==0 for i in range(selected_count))
                ws.add_dialog.hide();ws.show_tasks();assert ws.pages.currentIndex()==2 and window.task_dialog.parentWidget() is not None
                if not large:assert ws.enqueue(work['id'],[e['id'] for e in downloaded],{'height':1080,'strict':False,'kind':None})==0
                done.append(True)
            except BaseException as error:errors.append(traceback.format_exc())
            window.request_exit()
        def deadline():
            if not done and not errors:errors.append({'reason':'batch verification deadline','queue':snapshot(),'recent':observations})
            window.request_exit()
        QTimer.singleShot(10,drive);QTimer.singleShot(200,verify);QTimer.singleShot(45000 if large else 10000,deadline);return original()
    with patch.object(QApplication,'exec',side_effect=run),patch('engine.inspect_video',side_effect=inspect),patch('engine._perform_download',side_effect=perform),patch.object(QMessageBox,'warning',side_effect=lambda *args:errors.append(AssertionError(str(args)))):
        main.gui(Namespace(smoke_ui=not large,url=None,screenshot=None))
    assert not errors,errors
    assert done,'batch verification did not run'

if __name__=='__main__' and any(flag in sys.argv for flag in ('--check','--large','--cancel')):
    try:check()
    except BaseException:
        import traceback
        traceback.print_exc(file=sys.__stderr__);raise SystemExit(1)
