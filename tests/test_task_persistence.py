"""Task checkpoints remain valid when UI readers overlap worker writes."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import download_tasks as tasks

class TaskPersistence(unittest.TestCase):
    def test_ui_read_and_worker_save_do_not_fail_or_publish_partial_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder,patch('download_tasks.directory',return_value=Path(folder)):
            task=tasks.create({'url':'https://example.invalid/1','site':'test','title':'before'},'best',folder)
            target=tasks.task_file(task['id']);entered=threading.Event();release=threading.Event();errors=[];values=[]
            original=Path.read_text
            def held_read(path,*args,**kwargs):
                if path!=target:return original(path,*args,**kwargs)
                with path.open('r',encoding='utf-8') as handle:
                    entered.set();assert release.wait(3)
                    return handle.read()
            def reader():
                try:values.append(tasks.load(task['id'])['title'])
                except BaseException as error:errors.append(error)
            def writer():
                try:tasks.save(dict(task,title='after'))
                except BaseException as error:errors.append(error)
            with patch.object(Path,'read_text',held_read):
                a=threading.Thread(target=reader);a.start();self.assertTrue(entered.wait(3))
                b=threading.Thread(target=writer);b.start();time.sleep(.03);release.set();a.join(3);b.join(3)
            self.assertFalse(a.is_alive() or b.is_alive());self.assertEqual(errors,[])
            self.assertEqual(values,['before']);self.assertEqual(tasks.load(task['id'])['title'],'after')

    def test_same_task_concurrent_saves_publish_complete_checkpoints(self):
        with tempfile.TemporaryDirectory() as folder,patch('download_tasks.directory',return_value=Path(folder)):
            task=tasks.create({'url':'https://example.invalid/1','site':'test','title':'before'},'best',folder)
            start=threading.Barrier(6);errors=[]
            def writer(number):
                try:
                    start.wait(3)
                    for i in range(10):tasks.save(dict(task,title=f'writer-{number}-{i}',stage='x'*16384))
                except BaseException as error:errors.append(error)
            threads=[threading.Thread(target=writer,args=(i,)) for i in range(6)]
            for thread in threads:thread.start()
            for thread in threads:thread.join(5)
            self.assertFalse(any(thread.is_alive() for thread in threads));self.assertEqual(errors,[])
            saved=tasks.load(task['id']);self.assertIn(saved['title'],[f'writer-{i}-9' for i in range(6)])
            self.assertEqual(saved['stage'],'x'*16384);self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    @unittest.skipUnless(os.name=='nt','Windows denies replacement of an open reader')
    def test_brief_external_reader_allows_save_after_handle_closes(self):
        with tempfile.TemporaryDirectory() as folder,patch('download_tasks.directory',return_value=Path(folder)):
            task=tasks.create({'url':'https://example.invalid/1','site':'test','title':'before'},'best',folder)
            ready=threading.Event()
            def external_reader():
                with tasks.task_file(task['id']).open('r',encoding='utf-8'):
                    ready.set();time.sleep(.06)
            thread=threading.Thread(target=external_reader);thread.start();self.assertTrue(ready.wait(3))
            try:tasks.save(dict(task,title='after'))
            finally:thread.join(3)
            self.assertEqual(tasks.load(task['id'])['title'],'after');self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    @unittest.skipUnless(os.name=='nt','Windows sharing error')
    def test_permanent_replace_error_preserves_checkpoint_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as folder,patch('download_tasks.directory',return_value=Path(folder)):
            task=tasks.create({'url':'https://example.invalid/1','site':'test','title':'before'},'best',folder)
            with tasks.task_file(task['id']).open('r',encoding='utf-8'):
                with self.assertRaises(PermissionError):tasks.save(dict(task,title='after'))
            self.assertEqual(tasks.load(task['id'])['title'],'before');self.assertEqual(list(Path(folder).glob('*.tmp')),[])
