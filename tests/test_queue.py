import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication
import download_tasks as tasks
from download_queue import DownloadQueue
import engine
import preferences


class QueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def wait(self, predicate, timeout=8):
        deadline = time.monotonic()+timeout
        while not predicate() and time.monotonic()<deadline:
            self.app.processEvents(); time.sleep(.005)
        self.assertTrue(predicate(), 'scheduler timed out')

    def test_limits_queue_pause_cancel_and_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); registry=root/'tasks';registry.mkdir()
            with patch('download_tasks.directory',return_value=registry):
                queue=DownloadQueue(5); gates={}; started=[]; mutex=threading.Lock(); errors=[]
                queue.failed.connect(lambda task_id,message:errors.append(message))
                def perform(info, format_id, output, cookies, progress, log, cancel, work):
                    key=info['title']
                    with mutex: started.append(key)
                    while not gates[key].is_set():
                        engine._check_cancel(cancel);time.sleep(.005)
                    return {'path':str(root/(key+'.mp4')),'verified':True}
                with patch('engine._perform_download',side_effect=perform):
                    ids=[]
                    for index in range(12):
                        info={'url':f'https://example.com/{index}','title':str(index),'site':'test','formats':[{'id':'best'}]}
                        gates[str(index)]=threading.Event()
                        task=tasks.create(info,'best',str(root));ids.append(task['id'])
                        self.assertTrue(queue.enqueue(task['id'],[],info))
                        self.assertFalse(queue.enqueue(task['id'],[],info))
                    self.wait(lambda:len(started)==5)
                    self.assertEqual(len(queue.active),5);self.assertEqual(len(queue.waiting),7)
                    self.assertEqual([row['id'] for row in queue.rows()], ids)
                    queue.set_limit(10)
                    self.wait(lambda:len(started)==10)
                    self.assertEqual(len(queue.active),10);self.assertEqual(len(queue.waiting),2)
                    queue.set_limit(2)
                    self.assertEqual(len(queue.active),10)
                    for index in range(9): gates[str(index)].set()
                    self.wait(lambda:len(queue.active)==2 and len(started)==11)
                    queue.pause(ids[-1]);self.assertEqual(tasks.load(ids[-1])['state'],'paused')
                    queue.set_limit(10)
                    self.wait(lambda:len(started)==11)
                    queue.discard(ids[9])
                    self.wait(lambda:not tasks.task_file(ids[9]).exists())
                    queue.shutdown();self.wait(lambda:not queue.active)
                    self.assertFalse(queue.waiting)
                    for task in tasks.pending():self.assertNotEqual(task['state'],'running')
                    self.assertEqual(len(queue.completed_rows),9)
                    for value in (0,11,-1):
                        with self.assertRaises(ValueError):queue.set_limit(value)

    def test_preferences_persist_and_reject_invalid_values(self):
        with tempfile.TemporaryDirectory() as folder, patch('preferences.app_data_dir',return_value=Path(folder)):
            self.assertEqual(preferences.parallel_limit(),5)
            preferences.save_limit(10);self.assertEqual(preferences.parallel_limit(),10)
            for value in (0,11,True,'5'):
                with self.assertRaises(ValueError):preferences.save_limit(value)
            (Path(folder)/'settings.json').write_text('{bad')
            self.assertEqual(preferences.parallel_limit(),5)
