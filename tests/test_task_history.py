import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import download_tasks as tasks
from download_queue import DownloadQueue


class TaskHistory(unittest.TestCase):
    def test_terminal_records_persist_and_delete_never_deletes_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);registry=root/'tasks';registry.mkdir()
            with patch('download_tasks.directory',return_value=registry):
                info={'url':'https://example.com/watch/1','site':'test','title':'title','formats':[{'id':'best'}]}
                done=tasks.create(info,'best',str(root));output=root/'user.mp4';output.write_bytes(b'user file')
                cache=tasks.work_path(done);cache.mkdir();(cache/'partial').write_bytes(b'cached')
                done.update(state='completed',result={'path':str(output),'verified':True})
                tasks.save(done);tasks.finish(done)
                self.assertFalse(cache.exists());self.assertFalse(tasks.task_file(done['id']).exists())
                self.assertEqual(tasks.load_record(done['id'])['download_percent'],100)
                self.assertEqual(tasks.pending(),[])
                cancelled=tasks.create(info,'best',str(root));cache=tasks.work_path(cancelled);cache.mkdir()
                (cache/'partial').write_bytes(b'cached');tasks.discard(cancelled['id'])
                self.assertFalse(cache.exists());self.assertEqual(tasks.load_record(cancelled['id'])['state'],'cancelled')
                # A new queue reads disk rather than relying on the previous instance's memory.
                restored=DownloadQueue();self.assertEqual(len(restored.rows()),2)
                self.assertFalse(restored.active);self.assertFalse(restored.waiting)
                stale=DownloadQueue();stale.completed_rows[done['id']]=done
                restored.delete_record(done['id']);restored.delete_record(cancelled['id'])
                self.assertEqual(tasks.records(),[]);self.assertEqual(output.read_bytes(),b'user file')
                self.assertEqual(stale.rows(),[])

    def test_corrupt_history_and_terminal_checkpoint_are_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);registry=root/'tasks';registry.mkdir()
            with patch('download_tasks.directory',return_value=registry):
                info={'url':'https://example.com/1','site':'test','title':'title','formats':[{'id':'best'}]}
                task=tasks.create(info,'best',str(root));task['state']='completed';task['result']={'path':str(root/'file.mp4')}
                tasks.save(task)  # Simulates interruption before archive/cache cleanup.
                tasks.history_file('d'*32).write_text('{broken')
                self.assertEqual(len(tasks.records()),1);self.assertEqual(tasks.pending(),[])
                self.assertFalse(DownloadQueue().enqueue(task['id'],[]))
                tasks.delete_history(task['id']);self.assertEqual(tasks.records(),[])

    def test_unfinished_history_deletion_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder,patch('download_tasks.directory',return_value=Path(folder)):
            task=tasks.create({'url':'https://example.com/1','site':'test','title':'title'},'best',folder)
            with self.assertRaises(ValueError):tasks.delete_history(task['id'])
            self.assertTrue(tasks.task_file(task['id']).exists())

    def test_failed_cache_cleanup_does_not_archive_cancellation(self):
        with tempfile.TemporaryDirectory() as folder,patch('download_tasks.directory',return_value=Path(folder)):
            task=tasks.create({'url':'https://example.com/1','site':'test','title':'title'},'best',folder)
            tasks.work_path(task).mkdir()
            with patch('download_tasks.shutil.rmtree',side_effect=OSError('busy')):
                with self.assertRaises(OSError):tasks.discard(task['id'])
            self.assertEqual(tasks.load(task['id'])['state'],'paused')
            self.assertFalse(tasks.history_file(task['id']).exists())

    def test_malformed_optional_fields_are_safe_to_display(self):
        with tempfile.TemporaryDirectory() as folder,patch('download_tasks.directory',return_value=Path(folder)):
            task=tasks.create({'url':'https://example.com/1','site':'test','title':'title'},'best',folder)
            task.update(state='completed',download_percent='broken',created_at='broken',stage={})
            tasks.save(task)
            row=tasks.records()[0]
            self.assertIsNone(row['download_percent']);self.assertEqual(row['created_at'],0);self.assertEqual(row['stage'],'')
