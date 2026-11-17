import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from media_library import Catalogue
from library_transfer import move_library


class LibraryTransfer(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,WVD_DATA_DIR=str(self.root/'profile'));self.env.start()
        self.store=Catalogue();self.source=self.root/'source';self.dest=self.root/'destination'
        self.lib=self.store.library(str(self.source),'Test')
    def tearDown(self):
        self.store.close();self.env.stop();self.temp.cleanup()
    def test_whole_library_includes_unregistered_files_and_empty_directories(self):
        (self.source/'subtitles.srt').write_bytes(b'subtitle')
        (self.source/'empty').mkdir()
        result=move_library(self.store,self.lib,self.dest)
        self.assertTrue(result['success'],result)
        self.assertEqual((self.dest/'subtitles.srt').read_bytes(),b'subtitle')
        self.assertTrue((self.dest/'empty').is_dir())
        self.assertEqual(self.store.read()['libraries'][0]['path'],str(self.dest))
        self.assertFalse((self.source/'subtitles.srt').exists())
    def test_cancel_retains_source_and_can_retry_same_destination(self):
        (self.source/'film.mp4').write_bytes(b'film')
        cancelled=[False]
        def update(row):
            if row['phase']=='verify':cancelled[0]=True
        result=move_library(self.store,self.lib,self.dest,lambda:cancelled[0],update)
        self.assertTrue(result['cancelled'],result);self.assertEqual((self.source/'film.mp4').read_bytes(),b'film')
        retry=move_library(self.store,self.lib,self.dest)
        self.assertTrue(retry['success'],retry)
    def test_space_failure_and_unknown_destination_preserve_bytes(self):
        (self.source/'film.mp4').write_bytes(b'film')
        with patch('shutil.disk_usage',return_value=type('Space',(),{'free':0})()):result=move_library(self.store,self.lib,self.dest)
        self.assertFalse(result['success']);self.assertIn('空间',result['error'])
        self.dest.mkdir(exist_ok=True);(self.dest/'private').write_bytes(b'private')
        result=move_library(self.store,self.lib,self.dest)
        self.assertFalse(result['success']);self.assertEqual((self.dest/'private').read_bytes(),b'private')
        self.assertEqual((self.source/'film.mp4').read_bytes(),b'film')
    def test_nested_paths_rejected(self):
        self.assertFalse(move_library(self.store,self.lib,self.source/'nested')['success'])
        self.assertFalse(move_library(self.store,self.lib,self.root)['success'])
    def test_external_source_change_after_copy_is_retained(self):
        (self.source/'film.mp4').write_bytes(b'film')
        def update(row):
            if row['phase']=='mount':
                (self.source/'film.mp4').write_bytes(b'external edit')
                (self.source/'new.txt').write_bytes(b'new')
        result=move_library(self.store,self.lib,self.dest,progress=update)
        self.assertTrue(result['success'],result)
        self.assertEqual((self.source/'film.mp4').read_bytes(),b'external edit')
        self.assertEqual((self.source/'new.txt').read_bytes(),b'new')
        self.assertEqual((self.dest/'film.mp4').read_bytes(),b'film')
        self.assertTrue(result['retained_source'])
    def test_target_corruption_before_final_hash_preserves_source(self):
        (self.source/'film.mp4').write_bytes(b'film')
        def update(row):
            if row['phase']=='verify':(self.dest/'film.mp4').write_bytes(b'broken')
        result=move_library(self.store,self.lib,self.dest,progress=update)
        self.assertFalse(result['success']);self.assertEqual((self.source/'film.mp4').read_bytes(),b'film')
        self.assertEqual(self.store.read()['libraries'][0]['path'],str(self.source))
    def test_external_media_and_partial_task_cache(self):
        group={'site':'AGE动漫','key':'123','url':'https://www.agedm.io/play/123/1/1','title':'作品','entries':[{'key':'1','title':'第一集','url':'https://www.agedm.io/play/123/1/1'}]}
        wid,ids=self.store.ingest(group,'作品','第一季',self.lib)
        external=self.root/'external.mp4';external.write_bytes(b'external');self.store.attach(ids[0],external,True)
        import download_tasks as tasks
        task=tasks.create({'url':'https://www.agedm.io/play/123/1/1','site':'AGE动漫','title':'作品'},'source',self.store.output(wid))
        self.store.bind(ids[0],task['id'])
        cache=tasks.work_path(task);cache.mkdir(parents=True);(cache/'1.part').write_bytes(b'partial')
        result=move_library(self.store,self.lib,self.dest)
        self.assertTrue(result['success'],result)
        restored=tasks.load_record(task['id']);self.assertEqual(restored['state'],'paused')
        self.assertEqual((tasks.work_path(restored)/'1.part').read_bytes(),b'partial')
        self.assertTrue(Path(restored['output']).is_relative_to(self.dest));self.assertEqual(external.read_bytes(),b'external')
        self.assertEqual(self.store.episode(ids[0])['files'][0]['path'],str(external))

    def test_active_unbound_task_is_not_killed_or_moved(self):
        import download_tasks as tasks
        task=tasks.create({'url':'https://example.com/watch/1','site':'test','title':'test'},'source',self.source)
        task['state']='running';tasks.save(task)
        result=move_library(self.store,self.lib,self.dest)
        self.assertFalse(result['success']);self.assertIn('暂停',result['error'])
        self.assertEqual(tasks.load_record(task['id'])['state'],'running')
        task['state']='paused';tasks.save(task)
        with tasks.lock(task['id']):result=move_library(self.store,self.lib,self.dest)
        self.assertFalse(result['success']);self.assertFalse(self.dest.exists())
    def test_mount_failure_does_not_delete_originals(self):
        (self.source/'film.mp4').write_bytes(b'film')
        with patch.object(self.store,'mount_library',side_effect=OSError('index unavailable')):
            result=move_library(self.store,self.lib,self.dest)
        self.assertFalse(result['success']);self.assertEqual((self.source/'film.mp4').read_bytes(),b'film')
        self.assertEqual((self.dest/'film.mp4').read_bytes(),b'film')
        self.assertEqual(self.store.read()['libraries'][0]['path'],str(self.source))
    def test_links_are_rejected_without_touching_their_target(self):
        external=self.root/'private';external.mkdir();(external/'secret').write_bytes(b'secret')
        try:(self.source/'link').symlink_to(external,target_is_directory=True)
        except OSError:
            if os.name!='nt':raise
            import subprocess
            created=subprocess.run(['cmd','/c','mklink','/J',str(self.source/'link'),str(external)],capture_output=True)
            if created.returncode:self.skipTest('link creation unavailable')
        result=move_library(self.store,self.lib,self.dest)
        self.assertFalse(result['success']);self.assertEqual((external/'secret').read_bytes(),b'secret')
    def test_copy_io_failure_preserves_original_and_mount(self):
        media=self.source/'film.mp4';media.write_bytes(b'film')
        original=Path.open;reads=[0]
        def open_file(path,*args,**kwargs):
            if path==media and args and args[0]=='rb':
                reads[0]+=1
                if reads[0]==2:raise OSError('source read failed')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',open_file):result=move_library(self.store,self.lib,self.dest)
        self.assertFalse(result['success']);self.assertIn('source read failed',result['error'])
        self.assertEqual(media.read_bytes(),b'film')
        self.assertEqual(self.store.read()['libraries'][0]['path'],str(self.source))
    def test_source_changed_during_copy_is_not_committed(self):
        media=self.source/'film.mp4';media.write_bytes(b'film')
        def update(row):
            if row['phase']=='copy' and row['path']=='film.mp4':media.write_bytes(b'edited')
        result=move_library(self.store,self.lib,self.dest,progress=update)
        self.assertFalse(result['success']);self.assertEqual(media.read_bytes(),b'edited')
        self.assertEqual(self.store.read()['libraries'][0]['path'],str(self.source))
