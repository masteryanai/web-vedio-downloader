import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import urllib.request

import download_tasks as tasks
import engine
import http_resume


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.body = b'original media data' * 65536
        self.calls = []
        self.mode = 'range'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                owner.calls.append(self.headers.get('Range'))
                offset = int(self.headers['Range'].split('=')[1].split('-')[0]) if self.headers.get('Range') else 0
                if owner.mode == 'expired' or owner.mode == 'range-416' and offset:
                    self.send_response(403 if owner.mode == 'expired' else 416)
                    self.end_headers(); return
                continuation = offset and owner.mode not in ('ignore', 'changed')
                self.send_response(206 if continuation else 200)
                self.send_header('Content-Length', str(len(owner.body)-(offset if continuation else 0)))
                if owner.mode != 'no-validator':
                    self.send_header('ETag', '"new"' if owner.mode == 'changed' else '"media"')
                if continuation:
                    start = offset+1 if owner.mode == 'wrong-range' else offset
                    self.send_header('Content-Range', f'bytes {start}-{len(owner.body)-1}/{len(owner.body)}')
                self.end_headers()
                try:
                    if owner.mode == 'disconnect' and not offset:
                        self.wfile.write(owner.body[:len(owner.body)//2])
                        self.close_connection = True
                    else:
                        self.wfile.write(owner.body[offset:] if continuation else owner.body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/media'
        self.path = self.root/'source.mp4'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temp.cleanup()

    def transfer(self, event=None):
        event = event or threading.Event()
        http_resume.download(self.url, self.path, opener=urllib.request.build_opener(), headers={},
                             check=lambda: engine._check_cancel(event),
                             report=lambda *_: event.set() if getattr(self, 'pause', False) else None)

    def interrupt(self):
        self.pause = True
        with self.assertRaises(engine.DownloadCancelled): self.transfer()
        self.pause = False
        self.assertGreater(self.path.stat().st_size, 0)
        self.assertLess(self.path.stat().st_size, len(self.body))

    def test_range_continues_without_repeating_prefix(self):
        self.interrupt(); offset = self.path.stat().st_size
        self.transfer()
        self.assertEqual(self.calls[-1], f'bytes={offset}-')
        self.assertEqual(self.path.read_bytes(), self.body)
        before = len(self.calls); self.transfer()
        self.assertEqual(before, len(self.calls))

    def test_ignored_range_and_changed_resource_restart(self):
        for mode in ('ignore', 'changed'):
            with self.subTest(mode=mode):
                self.mode = 'range'; self.path.unlink(missing_ok=True)
                self.path.with_name(self.path.name+'.resume.json').unlink(missing_ok=True)
                self.interrupt(); self.mode = mode
                self.transfer()
                self.assertEqual(self.path.read_bytes(), self.body)

    def test_bad_range_is_never_appended(self):
        self.interrupt(); original = self.path.read_bytes(); self.mode = 'wrong-range'
        with self.assertRaises(ValueError): self.transfer()
        self.assertEqual(self.path.read_bytes(), original)

    def test_no_validator_restarts_safely(self):
        self.mode = 'no-validator'; self.interrupt(); self.transfer()
        self.assertIsNone(self.calls[-1])
        self.assertEqual(self.path.read_bytes(), self.body)

    def test_disconnect_retries_from_saved_offset(self):
        self.mode = 'disconnect'; self.transfer()
        self.assertTrue(any(value is not None for value in self.calls))
        self.assertEqual(self.path.read_bytes(), self.body)

    def test_expired_url_preserves_partial(self):
        self.interrupt(); original = self.path.read_bytes(); self.mode = 'expired'
        with self.assertRaisesRegex(ValueError, '过期'): self.transfer()
        self.assertEqual(self.path.read_bytes(), original)

    def test_range_416_restarts_safely(self):
        self.interrupt(); self.mode = 'range-416'; self.transfer()
        self.assertEqual(self.path.read_bytes(), self.body)

    def test_persist_pause_resume_and_scoped_delete(self):
        registry = self.root/'tasks'; registry.mkdir()
        output = self.root/'output'; output.mkdir()
        user_file = output/'my-video.mp4'; user_file.write_bytes(b'user content')
        info = {'url':'https://example.com/watch/1', 'site':'test', 'title':'title', '_engine':'haokan',
                'formats':[{'id':'best'}], '_media_url':'secret', 'cookies':'secret'}
        with patch('download_tasks.directory', return_value=registry):
            task = tasks.create(info, 'best', str(output)); task_id = task['id']
            self.assertNotIn('secret', tasks.task_file(task_id).read_text(encoding='utf-8'))
            def interrupted(info, selected, work, cookies, progress, log, cancel):
                (work/'source.part').write_bytes(b'partial')
                cancel.set(); engine._check_cancel(cancel)
            with patch('engine._download_haokan', side_effect=interrupted):
                with self.assertRaises(engine.DownloadCancelled):
                    engine.download_video(info, 'best', str(output), cancel=threading.Event(), task_id=task_id)
            self.assertEqual(tasks.load(task_id)['state'], 'paused')
            self.assertTrue((tasks.work_path(task)/'source.part').exists())
            self.assertEqual(tasks.pending()[0]['id'], task_id)
            with tasks.lock(task_id):
                with self.assertRaises(RuntimeError): tasks.discard(task_id)
            def completed(info, selected, work, *_):
                self.assertEqual((work/'source.part').read_bytes(), b'partial')
                target = work/'result.mp4'; target.write_bytes(b'completed'); return target
            with patch('engine._download_haokan', side_effect=completed), patch('engine._verify', return_value={'verified':True}), patch('site_registry.record_success'):
                result = engine.download_video(info, 'best', str(output), task_id=task_id)
            self.assertTrue(Path(result['path']).exists())
            self.assertFalse(tasks.task_file(task_id).exists())
            self.assertFalse(tasks.work_path(task).exists())
            other = tasks.create(info, 'best', str(output)); tasks.work_path(other).mkdir()
            (tasks.work_path(other)/'partial').write_bytes(b'x')
            tasks.discard(other['id'])
            self.assertEqual(user_file.read_bytes(), b'user content')
            self.assertTrue(Path(result['path']).exists())
            with self.assertRaises(ValueError): tasks.task_file('../unsafe')


if __name__ == '__main__': unittest.main()
