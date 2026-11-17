import os,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
import engine,download_tasks as tasks

class VerificationResume(unittest.TestCase):
    def test_cancel_then_continue_uses_local_media_without_source_or_download(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,WVD_DATA_DIR=str(Path(folder)/'data')):
            root=Path(folder);cancel=threading.Event();events=[]
            info={'site':'test','url':'https://example.org/one','title':'One','duration':10,'_engine':'haokan','formats':[{'id':'best'}]}
            task=tasks.create(info,'best',root/'output')
            def download(*args):
                media=tasks.work_path(task)/'ready.mp4';media.write_bytes(b'preserve local video');return media
            def interrupted(*args):cancel.set();raise engine.DownloadCancelled()
            with patch('engine._download_haokan',side_effect=download),patch('engine._verify',side_effect=interrupted):
                with self.assertRaises(engine.DownloadCancelled):engine.download_video(info,'best',task['output'],progress=events.append,cancel=cancel,task_id=task['id'])
            saved=tasks.load(task['id']);self.assertIn('verification',saved);self.assertEqual(saved['stage'],'待校验')
            media=tasks.work_path(saved)/'ready.mp4';self.assertEqual(media.read_bytes(),b'preserve local video')
            with patch('engine.inspect_video',side_effect=AssertionError('network parse')),patch('engine._perform_download',side_effect=AssertionError('redownload')),patch('engine._verify',return_value={'verified':True,'duration':10}):
                result=engine.resume_verification(saved,events.append,threading.Event())
            self.assertEqual(Path(result['path']).read_bytes(),b'preserve local video')
            self.assertEqual(tasks.load_record(task['id'])['state'],'completed')

    def test_changed_pending_file_is_preserved_and_rejected(self):
        from verification_state import capture,validate
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,WVD_DATA_DIR=str(Path(folder)/'data')):
            info={'site':'test','url':'https://example.org/one','title':'One'};task=tasks.create(info,'best',Path(folder)/'output');work=tasks.work_path(task);work.mkdir(parents=True)
            media=work/'ready.mp4';media.write_bytes(b'original');task['verification']=capture(work,media,info,{},'.mp4')
            media.write_bytes(b'changed file')
            with self.assertRaisesRegex(ValueError,'改变'):validate(task)
            self.assertEqual(media.read_bytes(),b'changed file')
