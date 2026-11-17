import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import download_tasks as tasks
import library_tasks

class LibraryTasksTests(unittest.TestCase):
    def test_pending_verification_survives_portable_library_copy(self):
        import shutil
        from verification_state import capture,validate
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,WVD_DATA_DIR=str(Path(folder)/'data')):
            base=Path(folder);old=base/'old';old.mkdir();new=base/'new'
            task=tasks.create({'url':'https://example.com/watch/1','site':'example','title':'Pending'},'hd',old/'season')
            cache=tasks.work_path(task);cache.mkdir(parents=True);media=cache/'ready.mp4';media.write_bytes(b'pending-media')
            task.update(state='paused',stage='待校验',phase='verify',library_episode='e'*32,verification=capture(cache,media,{'duration':10},{'height':720},'.mp4'));tasks.save(task)
            context={'works':[{'id':'w','library_id':'lib'}],'episodes':[{'id':'e'*32,'work_id':'w','task_ids':[task['id']]}]}
            rows=library_tasks.snapshot_tasks('lib',old,context);self.assertNotIn(str(old),str(rows[0]['verification']))
            shutil.copytree(old,new);mapping=library_tasks.restore_tasks(rows,new,{task['id']:'b'*32})
            restored=tasks.load(mapping[task['id']]);path,value=validate(restored)
            self.assertTrue(path.is_relative_to(new));self.assertEqual(path.read_bytes(),b'pending-media');self.assertEqual(value['duration'],10)

    def test_move_restores_paused_task_at_new_root_without_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); old=base/'old'; new=base/'new';old.mkdir()
            with patch.dict(os.environ,WVD_DATA_DIR=str(base/'data')):
                task=tasks.create({'url':'https://example.com/watch/1','site':'example','title':'Title'},'hd',old/'season')
                task.update(state='running',library_episode='e'*32,cookies='secret',_media_url='secret');tasks.save(task)
                context={'works':[{'id':'w','library_id':'lib'}],'episodes':[{'id':'e'*32,'work_id':'w','task_ids':[task['id']]}]}
                rows=library_tasks.snapshot_tasks('lib',old,context)
                self.assertNotIn('secret',str(rows))
                old.rename(new)
                mapping=library_tasks.restore_tasks(rows,new,{})
                restored=tasks.load(mapping[task['id']])
                self.assertEqual(restored['state'],'paused')
                self.assertEqual(restored['output'],str(new/'season'))
                self.assertEqual(restored['library_episode'],'e'*32)

    def test_clone_has_independent_identity_and_cache(self):
        import shutil
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);old=base/'old';old.mkdir();new=base/'new'
            with patch.dict(os.environ,WVD_DATA_DIR=str(base/'data')):
                task=tasks.create({'url':'https://example.com/watch?id=1&token=secret','site':'example','title':'T'},'hd',old/'season')
                task.update(library_episode='e'*32);tasks.save(task)
                cache=tasks.work_path(task);cache.mkdir(parents=True);(cache/'source.part').write_bytes(b'prefix')
                context={'works':[{'id':'w','library_id':'lib'}],'episodes':[{'id':'e'*32,'work_id':'w','task_ids':[task['id']]}]}
                rows=library_tasks.snapshot_tasks('lib',old,context);shutil.copytree(old,new)
                newid='b'*32;mapping=library_tasks.restore_tasks(rows,new,{task['id']:newid,'e'*32:'f'*32})
                cloned=tasks.load(mapping[task['id']]);self.assertEqual(cloned['library_episode'],'f'*32)
                self.assertEqual(tasks.work_path(cloned).joinpath('source.part').read_bytes(),b'prefix')
                tasks.work_path(cloned).joinpath('source.part').write_bytes(b'clone')
                self.assertEqual((cache/'source.part').read_bytes(),b'prefix')
                self.assertEqual(tasks.load(task['id'])['output'],str(old/'season'))
                self.assertEqual(cloned['url'],'https://example.com/watch?id=1')

    def test_invalid_later_row_causes_no_task_or_cache_writes(self):
        import copy
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);base.joinpath('lib').mkdir()
            with patch.dict(os.environ,WVD_DATA_DIR=str(base/'data')):
                task=tasks.create({'url':'https://example.com/watch','site':'example','title':'T'},'hd',base/'lib')
                context={'works':[{'id':'w','library_id':'lib'}],'episodes':[{'id':'e','work_id':'w','task_ids':[task['id']]}]}
                rows=library_tasks.snapshot_tasks('lib',base/'lib',context);bad=copy.deepcopy(rows[0]);bad['id']='c'*32;bad['output']={'kind':'relative','path':'../escape'}
                before=tasks.task_file(task['id']).read_bytes()
                with self.assertRaises(ValueError):library_tasks.restore_tasks(rows+[bad],base/'lib',{})
                self.assertEqual(tasks.task_file(task['id']).read_bytes(),before)
                self.assertFalse(tasks.task_file('c'*32).exists())

    def test_active_original_task_blocks_relocation(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);base.joinpath('lib').mkdir()
            with patch.dict(os.environ,WVD_DATA_DIR=str(base/'data')):
                task=tasks.create({'url':'https://example.com/watch','site':'example','title':'T'},'hd',base/'lib')
                context={'works':[{'id':'w','library_id':'lib'}],'episodes':[{'id':'e','work_id':'w','task_ids':[task['id']]}]}
                rows=library_tasks.snapshot_tasks('lib',base/'lib',context)
                with tasks.lock(task['id']):
                    with self.assertRaises(RuntimeError):library_tasks.restore_tasks(rows,base/'lib',{task['id']:task['id']})

from tests.test_resume import ResumeTests
import json
import urllib.request

class RestoredRangeTests(ResumeTests):
    def test_move_continues_existing_bytes_and_refuses_restarts(self):
        self.interrupt();prefix=self.path.read_bytes();offset=len(prefix)
        moved=self.root/'moved';moved.mkdir()
        original=self.path;sidecar=original.with_name(original.name+'.resume.json')
        self.path=moved/original.name;original.rename(self.path);sidecar.rename(self.path.with_name(self.path.name+'.resume.json'))
        import http_resume
        def transfer():
            http_resume.download(self.url,self.path,opener=urllib.request.build_opener(),headers={},check=lambda:None,report=lambda *_:None,preserve_existing=True)
        for mode in ('ignore','changed','range-416','expired'):
            self.mode=mode
            with self.assertRaises(ValueError):transfer()
            self.assertEqual(self.path.read_bytes(),prefix)
        self.mode='range';transfer()
        self.assertEqual(self.calls[-1],f'bytes={offset}-')
        self.assertEqual(self.path.read_bytes(),self.body)

    def test_missing_validator_keeps_partial_file(self):
        self.mode='no-validator';self.interrupt();prefix=self.path.read_bytes()
        import http_resume
        with self.assertRaisesRegex(ValueError,'旧片段已保留'):
            http_resume.download(self.url,self.path,opener=urllib.request.build_opener(),headers={},check=lambda:None,report=lambda *_:None,preserve_existing=True)
        self.assertEqual(self.path.read_bytes(),prefix)

class RestoredEngineTests(unittest.TestCase):
    def test_old_ytdlp_piece_is_preserved_and_never_published_as_media(self):
        import engine
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            with patch.dict(os.environ,WVD_DATA_DIR=str(base/'data')):
                info={'url':'https://example.com/watch','site':'example','title':'T','_engine':'yt-dlp','formats':[{'id':'hd'}]}
                task=tasks.create(info,'hd',base/'lib');task['restored_cache']=True;tasks.save(task)
                cache=tasks.work_path(task);cache.mkdir(parents=True);piece=cache/'download.mp4.part';piece.write_bytes(b'not a full media file')
                with self.assertRaisesRegex(engine.VideoError,'旧片段已保留'):
                    engine.download_video(info,'hd',base/'lib',task_id=task['id'])
                self.assertEqual(piece.read_bytes(),b'not a full media file')
                self.assertEqual(list((base/'lib').glob('*.mp4')),[])

class RestoreTransactionTests(unittest.TestCase):
    def test_external_commit_failure_rolls_back_exact_checkpoint_and_clone_cache(self):
        import shutil
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);old=base/'old';new=base/'new';old.mkdir()
            with patch.dict(os.environ,WVD_DATA_DIR=str(base/'data')):
                task=tasks.create({'url':'https://example.com/watch','site':'example','title':'T'},'hd',old)
                task.update(library_episode='e');tasks.save(task)
                cache=tasks.work_path(task);cache.mkdir();(cache/'piece.part').write_bytes(b'prefix')
                rows=library_tasks.snapshot_tasks('lib',old,{'works':[{'id':'w','library_id':'lib'}],'episodes':[{'id':'e','work_id':'w','task_ids':[task['id']]}]})
                shutil.copytree(old,new);newid='d'*32;path=tasks.task_file(newid)
                with self.assertRaisesRegex(RuntimeError,'archive failed'):
                    with library_tasks.restore_transaction(rows,new,{task['id']:newid}) as mapping:
                        self.assertTrue(tasks.task_file(newid).exists())
                        self.assertTrue((new/('.video-downloader-'+newid)/'piece.part').exists())
                        with self.assertRaises(RuntimeError):
                            with tasks.lock(newid):pass
                        raise RuntimeError('archive failed')
                self.assertFalse(tasks.task_file(newid).exists())
                self.assertEqual((new/cache.name/'piece.part').read_bytes(),b'prefix')
                self.assertEqual((cache/'piece.part').read_bytes(),b'prefix')

    def test_unbound_paused_task_output_moves_and_checkpoint_rollback_is_exact(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);old=base/'old';new=base/'new';old.mkdir()
            with patch.dict(os.environ,WVD_DATA_DIR=str(base/'data')):
                task=tasks.create({'url':'https://example.com/watch','site':'example','title':'T'},'hd',old/'season')
                rows=library_tasks.snapshot_tasks('lib',old,{'works':[],'episodes':[]})
                self.assertEqual(len(rows),1)
                path=tasks.task_file(task['id']);original=path.read_bytes()+b'\n';path.write_bytes(original)
                old.rename(new)
                with self.assertRaises(RuntimeError):
                    with library_tasks.restore_transaction(rows,new,{task['id']:task['id']}):raise RuntimeError('index failed')
                self.assertEqual(path.read_bytes(),original)
                library_tasks.restore_tasks(rows,new,{task['id']:task['id']})
                self.assertEqual(tasks.load(task['id'])['output'],str(new/'season'))

    @unittest.skipUnless(os.name=='nt','Windows directory junction behavior')
    def test_junction_in_output_is_rejected_without_writing_outside(self):
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);root=base/'lib';outside=base/'outside';root.mkdir();outside.mkdir();link=root/'linked'
            subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],check=True,capture_output=True)
            try:
                with patch.dict(os.environ,WVD_DATA_DIR=str(base/'data')):
                    row={'id':'d'*32,'url':'https://example.com/watch','site':'s','title':'T','format_id':'hd','state':'paused','output':{'kind':'relative','path':'linked/sub'},'cache':{'kind':'relative','path':'linked/sub/.video-downloader-'+('d'*32)}}
                    with self.assertRaises(ValueError):library_tasks.restore_tasks([row],root,{})
                    self.assertEqual(list(outside.iterdir()),[])
                    self.assertFalse(tasks.task_file('d'*32).exists())
            finally:link.rmdir()
