import hashlib
import io
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
import updater


class UpdateDownload(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.exe=self.root/'installed'/'WebVideoDownloader.exe';self.exe.parent.mkdir();self.exe.write_bytes(b'old')
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:z.writestr('WebVideoDownloader/WebVideoDownloader.exe',os.urandom(1024*1024))
        self.body=stream.getvalue();self.calls=[];self.mode='drop';owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                offset=int(self.headers.get('Range','bytes=0-')[6:-1]);owner.calls.append(offset)
                if owner.mode=='ignore':offset=0
                self.send_response(206 if offset else 200)
                self.send_header('Content-Length',str(len(owner.body)-offset))
                if offset:self.send_header('Content-Range',f'bytes {offset+(1 if owner.mode=="bad-range" else 0)}-{len(owner.body)-1}/{len(owner.body)}')
                self.end_headers()
                data=owner.body[offset:]
                try:
                    if (owner.mode=='drop' and len(owner.calls)==1) or owner.mode=='always-drop':data=data[:len(data)*55//100]
                    if owner.mode=='stall' and len(owner.calls)==1:
                        self.wfile.write(data[:65536]);self.wfile.flush();time.sleep(.7)
                    elif owner.mode=='slow':
                        for i in range(0,len(data),1024):
                            self.wfile.write(data[i:i+1024]);self.wfile.flush();time.sleep(.02)
                    else:
                        self.wfile.write(data);self.wfile.flush()
                    self.close_connection=True
                except OSError:pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.info={'url':updater.PREFIX+'v9.0.0/test.zip','version':'9.0.0','manifest':{'size':len(self.body),'sha256':hashlib.sha256(self.body).hexdigest()}}
        def request(url,asset=False,headers=None,timeout=None):
            return urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}/package',headers=headers or {}),timeout=timeout or 2)
        self.patches=[patch('updater.request',side_effect=request),patch('updater.app_data_dir',return_value=self.root/'appdata'),patch('updater.sys.frozen',True,create=True),patch('updater.sys.executable',str(self.exe))]
        for item in self.patches:item.start()

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def test_disconnect_at_55_percent_retries_from_saved_bytes(self):
        result=updater.prepare_update(self.info,threading.Event(),lambda _:None)
        self.assertTrue(Path(result['plan']).exists())
        self.assertGreater(len(self.calls),1)
        self.assertGreater(self.calls[1],0)
        self.assertEqual(self.exe.read_bytes(),b'old')

    def test_cancel_then_retry_resumes_existing_partial(self):
        self.mode='normal';cancel=threading.Event()
        def report(value):
            if value.get('bytes',0)>0:cancel.set()
        with self.assertRaisesRegex(RuntimeError,'取消'):updater.prepare_update(self.info,cancel,report)
        updater.prepare_update(self.info,threading.Event(),lambda _:None)
        self.assertGreater(self.calls[-1],0)

    def test_reused_verified_package_has_independent_install_plans(self):
        import json
        self.mode='normal'
        first=updater.prepare_update(self.info,threading.Event(),lambda _:None)
        second=updater.prepare_update(self.info,threading.Event(),lambda _:None)
        self.assertNotEqual(first['plan'],second['plan'])
        self.assertEqual(json.loads(Path(first['plan']).read_text())['archive'],json.loads(Path(second['plan']).read_text())['archive'])
        self.assertEqual(len(self.calls),1)

    def test_cancel_during_validation_never_creates_install_plan(self):
        self.mode='normal';cancel=threading.Event()
        def report(value):
            if value.get('percent')==97:cancel.set()
        with self.assertRaisesRegex(RuntimeError,'取消'):
            updater.prepare_update(self.info,cancel,report)
        stage=self.root/'appdata'/'updates'/('package-'+self.info['manifest']['sha256'])
        self.assertFalse((stage/'plan.json').exists())
        self.assertEqual(self.exe.read_bytes(),b'old')

    def test_exhausted_retries_preserve_partial_and_do_not_install(self):
        self.mode='always-drop'
        with self.assertRaisesRegex(RuntimeError,'重试已用尽'):
            updater.prepare_update(self.info,threading.Event(),lambda _:None)
        self.assertEqual(len(self.calls),4)
        self.assertTrue(all(offset>0 for offset in self.calls[1:]))
        self.assertEqual(self.exe.read_bytes(),b'old')
        stage=self.root/'appdata'/'updates'/('package-'+self.info['manifest']['sha256'])
        self.assertGreater((stage/'package.zip').stat().st_size,0)
        self.assertFalse((stage/'plan.json').exists())

    def test_stalled_socket_retries_and_reports_recovery(self):
        self.mode='stall';events=[]
        with patch('update_transfer.READ_TIMEOUT',.15):
            updater.prepare_update(self.info,threading.Event(),events.append)
        self.assertGreater(self.calls[1],0)
        self.assertTrue(any('重试' in row['stage'] for row in events))

    def test_slow_trickle_reports_and_cancels_without_filling_buffer(self):
        self.mode='slow';cancel=threading.Event();started=time.monotonic()
        def report(value):
            if value.get('bytes',0)>0:cancel.set()
        with self.assertRaisesRegex(RuntimeError,'取消'):updater.prepare_update(self.info,cancel,report)
        self.assertLess(time.monotonic()-started,.5)

    def test_server_ignores_range_restarts_without_appending(self):
        self.test_cancel_then_retry_resumes_existing_partial()
        stage=self.root/'appdata'/'updates'/('package-'+self.info['manifest']['sha256'])
        (stage/'package.zip').write_bytes(self.body[:65536]);self.mode='ignore'
        updater.prepare_update(self.info,threading.Event(),lambda _:None)
        self.assertEqual((stage/'package.zip').read_bytes(),self.body)

    def test_invalid_range_is_rejected_and_original_is_untouched(self):
        self.test_cancel_then_retry_resumes_existing_partial()
        stage=self.root/'appdata'/'updates'/('package-'+self.info['manifest']['sha256'])
        (stage/'package.zip').write_bytes(self.body[:65536]);self.mode='bad-range'
        with self.assertRaisesRegex(RuntimeError,'范围'):updater.prepare_update(self.info,threading.Event(),lambda _:None)
        self.assertEqual(self.exe.read_bytes(),b'old')

    def test_corrupt_complete_cache_is_rejected_then_retry_succeeds(self):
        self.mode='normal'
        updater.prepare_update(self.info,threading.Event(),lambda _:None)
        stage=self.root/'appdata'/'updates'/('package-'+self.info['manifest']['sha256'])
        (stage/'package.zip').write_bytes(b'x'*len(self.body))
        with self.assertRaisesRegex(RuntimeError,'SHA-256'):updater.prepare_update(self.info,threading.Event(),lambda _:None)
        self.assertFalse((stage/'package.zip').exists())
        updater.prepare_update(self.info,threading.Event(),lambda _:None)


if __name__=='__main__':unittest.main()
