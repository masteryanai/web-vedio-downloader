import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import download_tasks as tasks
from download_queue import DownloadQueue


class TaskHistory(unittest.TestCase):
    def test_history_deletion_modes_never_delete_completed_or_other_files(self):
        for clean in (False,True):
            with self.subTest(clean=clean),tempfile.TemporaryDirectory() as folder,patch('download_tasks.directory',return_value=Path(folder)/'tasks'):
                root=Path(folder);(root/'tasks').mkdir()
                task=tasks.create({'url':'https://example.com/1','site':'test','title':'title'},'best',folder)
                video=root/'finished.mp4';video.write_bytes(b'completed')
                other=root/'other.mp4';other.write_bytes(b'other')
                cache=tasks.work_path(task);cache.mkdir();(cache/'source.part').write_bytes(b'partial')
                task.update(state='completed',result={'path':str(video)})
                tasks.save(task);tasks.archive(task)
                tasks.delete_history(task['id'],clean_cache=clean)
                self.assertEqual(cache.exists(),not clean)
                self.assertEqual(video.read_bytes(),b'completed');self.assertEqual(other.read_bytes(),b'other')
                self.assertFalse(tasks.history_file(task['id']).exists())
                self.assertFalse(tasks.task_file(task['id']).exists())

    def test_cache_cleanup_failure_preserves_history(self):
        with tempfile.TemporaryDirectory() as folder,patch('download_tasks.directory',return_value=Path(folder)):
            task=tasks.create({'url':'https://example.com/1','site':'test','title':'title'},'best',folder)
            task['state']='cancelled';tasks.save(task);tasks.archive(task);tasks.work_path(task).mkdir()
            with patch('download_tasks.shutil.rmtree',side_effect=OSError('busy')):
                with self.assertRaises(OSError):tasks.delete_history(task['id'],clean_cache=True)
            self.assertEqual(tasks.load_record(task['id'])['state'],'cancelled')
            self.assertTrue(tasks.history_file(task['id']).exists())

    def test_completed_file_inside_cache_blocks_cleanup(self):
        with tempfile.TemporaryDirectory() as folder,patch('download_tasks.directory',return_value=Path(folder)):
            task=tasks.create({'url':'https://example.com/1','site':'test','title':'title'},'best',folder)
            cache=tasks.work_path(task);cache.mkdir();video=cache/'finished.mp4';video.write_bytes(b'completed')
            task.update(state='completed',result={'path':str(video)});tasks.save(task);tasks.archive(task)
            with self.assertRaisesRegex(ValueError,'成品位于缓存'):tasks.delete_history(task['id'],clean_cache=True)
            self.assertTrue(tasks.history_file(task['id']).exists());self.assertEqual(video.read_bytes(),b'completed')
            tasks.delete_history(task['id']);self.assertEqual(video.read_bytes(),b'completed')

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
