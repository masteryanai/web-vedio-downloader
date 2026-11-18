import os,subprocess,sys,tempfile,unittest
from pathlib import Path

class AddFlowUI(unittest.TestCase):
    def test_manager_multiple_links_and_explicit_validation(self):self.run_product('manager')
    def test_lite_multiple_links_without_library_and_explicit_validation(self):self.run_product('lite')
    def run_product(self,product):
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,WVD_DATA_DIR=str(Path(folder)/'data'),WVD_PRODUCT=product,QT_QPA_PLATFORM='offscreen',PYTHONUTF8='1')
            result=subprocess.run([sys.executable,'-X','utf8',__file__,'--check',product],env=env,capture_output=True,text=True,timeout=45)
            diagnostics=Path(folder)/'data/diagnostics/runtime.log'
            evidence=diagnostics.read_text(encoding='utf-8',errors='replace')[-12000:] if result.returncode and diagnostics.exists() else ''
            self.assertEqual(result.returncode,0,result.stdout+result.stderr+evidence)

def check(product):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import time,traceback
    from argparse import Namespace
    from unittest.mock import patch
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    import main
    root=Path(os.environ['WVD_DATA_DIR']).parent;errors=[];done=[];original=QApplication.exec
    def inspect(url,*args):
        key='one' if '/one' in url else 'two'
        return {'site':'B站','url':url,'title':key,'duration':1,'formats':[{'id':'best','label':'1080P','height':1080,'width':1920}],
                '_collection':{'site':'B站','url':url,'key':key,'title':key,'kind':'video','entries':[{'key':key,'title':key,'url':url}]}}
    def perform(info,format_id,output,*args,**kwargs):
        path=Path(output)/(info['title']+'.mp4');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'isolated workflow fixture');return {'path':str(path),'verified':True}
    def execute():
        app=QApplication.instance();window=next(w for w in app.topLevelWidgets() if type(w).__name__=='Window');ws=window.workspace
        def drive():
            try:
                if product=='manager':ws.store.library(root/'media','Test')
                else:window.output_path.setText(str(root/'media'))
                ws.open_add();app.processEvents();assert window.url_input.isVisible() and not ws.add_controls.isVisible()
                ws.add_flow.start_urls(['https://www.bilibili.com/video/one','https://www.bilibili.com/video/two'])
                deadline=time.monotonic()+12
                while time.monotonic()<deadline and (ws.add_flow.active_source is not None or any(row['status']=='pending' for row in ws.add_flow.sources)):
                    app.processEvents();time.sleep(.01)
                assert all(row['status']=='ready' for row in ws.add_flow.sources),ws.add_flow.sources
                assert ws.add_controls.isVisible() and not window.url_input.isVisible()
                window.download();deadline=time.monotonic()+12
                while time.monotonic()<deadline and (window.queue.active or window.queue.waiting):app.processEvents();time.sleep(.01)
                assert len(window.queue.completed_rows)==2,window.queue.rows()
                assert not ws.add_dialog.isVisible()
                ws.add_flow.start_urls(['https://www.douyin.com/video/1234567890123456789'],automatic=True)
                deadline=time.monotonic()+3
                while time.monotonic()<deadline and ws.add_flow.active_source is not None:app.processEvents();time.sleep(.01)
                assert ws.add_flow.sources[0]['status']=='validation' and window.browser_dialog is None
                assert ws.add_flow.validate.isVisible()
                ws.add_dialog.hide();done.append(True)
            except BaseException:errors.append(traceback.format_exc())
            window.request_exit()
        QTimer.singleShot(100,drive);QTimer.singleShot(35000,window.request_exit);return original()
    with patch.object(QApplication,'exec',side_effect=execute),patch('collection_media.inspect_collection',side_effect=inspect),patch('engine._perform_download',side_effect=perform):main.gui(Namespace(smoke_ui=False,url=None,screenshot=None))
    assert not errors,errors
    assert done

if __name__=='__main__':
    if '--check' in sys.argv:
        try:check(sys.argv[-1])
        except BaseException:
            import traceback
            traceback.print_exc(file=sys.__stderr__);raise SystemExit(1)
    else:unittest.main()
