import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
import zipfile
import diagnostics as d


class Diagnostics(unittest.TestCase):
    def wait(self,predicate):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.02)
        self.fail('diagnostic feedback did not arrive')

    def test_redaction_before_persistence_and_export_whitelist(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'diagnostics';root.mkdir()
            rec=d.Recorder(root,spawn=False)
            rec.event('test','Cookie: COOKIE_SECRET; other=OTHER_SECRET\nAuthorization: Bearer AUTH_SECRET\n{"token":"TOKEN_SECRET"}\nhttps://user:URL_SECRET@example.com/private?key=QUERY_SECRET\nC:\\Users\\PERSONAL_NAME\\video.mp4')
            rec.stop()
            raw=(root/'runtime.log').read_text(encoding='utf-8')
            for line in raw.splitlines():self.assertIn('run_id',json.loads(line))
            for secret in ('COOKIE_SECRET','OTHER_SECRET','AUTH_SECRET','TOKEN_SECRET','URL_SECRET','QUERY_SECRET','PERSONAL_NAME'):
                self.assertNotIn(secret,raw)
            (root/'cookies.json').write_text('COOKIE_DATABASE_SECRET')
            (root/'video.mp4').write_bytes(b'MEDIA_SECRET')
            with patch('diagnostics.root',return_value=root),patch('download_tasks.records',return_value=[]):
                report=d.export_bundle(Path(folder)/'report.zip','https://example.com/path?secret=DESC_SECRET')
            with zipfile.ZipFile(Path(folder)/'report.zip') as z:
                self.assertEqual(set(z.namelist()),{'summary.json','runtime.log','threads.json'})
                all_data=b''.join(z.read(name) for name in z.namelist()).decode()
                for secret in ('COOKIE_DATABASE_SECRET','MEDIA_SECRET','DESC_SECRET','PERSONAL_NAME'):self.assertNotIn(secret,all_data)
                self.assertEqual(json.loads(z.read('summary.json'))['report_id'],report)

    def test_multiword_credentials_and_uppercase_urls_are_redacted(self):
        text='password=FIRST_WORD SECOND_WORD\nHTTPS://example.com/path?token=URL_WORD\n\\\\server\\share\\PRIVATE_USER\\video.mp4'
        clean=d.redact(text)
        for secret in ('FIRST_WORD','SECOND_WORD','URL_WORD','PRIVATE_USER'):self.assertNotIn(secret,clean)
        clean=d.scrub({'headers':{'Cookie':'AUTH_HEADER_SECRET'},'token':'JSON_TOKEN_SECRET'})
        self.assertNotIn('AUTH_HEADER_SECRET',str(clean));self.assertNotIn('JSON_TOKEN_SECRET',str(clean))

    def test_rotation_keeps_five_files_and_parallel_writes(self):
        with tempfile.TemporaryDirectory() as folder,patch('diagnostics.LOG_SIZE',600):
            root=Path(folder)
            def write():
                for _ in range(30):d.append_log(root,'event '+'x'*100)
            threads=[threading.Thread(target=write) for _ in range(3)]
            for thread in threads:thread.start()
            for thread in threads:thread.join()
            files=list(root.glob('runtime.log*'))
            self.assertEqual(len(files),5)
            self.assertTrue(all(path.stat().st_size<=600 for path in files))

    def test_multiple_processes_share_bounded_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            script="import sys;from pathlib import Path;import diagnostics as d;d.LOG_SIZE=600;[d.append_log(Path(sys.argv[1]),'x'*100) for _ in range(25)]"
            children=[subprocess.Popen([sys.executable,'-c',script,folder]) for _ in range(3)]
            self.assertEqual([child.wait(timeout=5) for child in children],[0,0,0])
            files=list(Path(folder).glob('runtime.log*'));self.assertEqual(len(files),5)
            self.assertTrue(all(p.stat().st_size<=600 for p in files))

    def test_disk_failure_does_not_escape_event_path(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('diagnostics.append_log',side_effect=OSError('disk full')):
                rec=d.Recorder(Path(folder),spawn=False);rec.event('test');rec.stop()
            self.assertGreater(rec.dropped,0)

    def test_watchdog_captures_stall_recovery_and_abnormal_exit(self):
        with tempfile.TemporaryDirectory() as folder,patch('diagnostics.root',return_value=Path(folder)):
            root=Path(folder);session=root/'sessions'/'test';session.mkdir(parents=True)
            d.atomic_json(session/'meta.json',{'pid':123,'run_id':'test','version':'test'})
            snapshot={'sequence':1,'sampled_at':time.time(),'threads':[{'frames':['main.py:1 blocked_ui']}],'phase':'inspect'}
            d.atomic_json(session/'heartbeat.json',snapshot);running=[True]
            with patch('diagnostics.alive',side_effect=lambda _:running[0]):
                monitor=threading.Thread(target=d.watchdog,args=(session,.12,.02));monitor.start()
                self.wait(lambda:bool(list(root.glob('incident-*.json'))))
                path=next(root.glob('incident-*.json'));data=json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(data['kind'],'ui_unresponsive');self.assertEqual(data['last_snapshot']['threads'],snapshot['threads'])
                snapshot['sequence']=2;d.atomic_json(session/'heartbeat.json',snapshot)
                self.wait(lambda:'recovered_at' in json.loads(path.read_text(encoding='utf-8')))
                running[0]=False;monitor.join(2);self.assertFalse(monitor.is_alive())
                kinds={json.loads(p.read_text(encoding='utf-8'))['kind'] for p in root.glob('incident-*.json')}
                self.assertEqual(kinds,{'ui_unresponsive','abnormal_exit'})

    def test_healthy_ui_long_background_task_does_not_report(self):
        with tempfile.TemporaryDirectory() as folder,patch('diagnostics.root',return_value=Path(folder)),patch('diagnostics.alive',return_value=True):
            root=Path(folder);session=root/'sessions'/'test';session.mkdir(parents=True)
            d.atomic_json(session/'meta.json',{'pid':123,'run_id':'test','version':'test'})
            monitor=threading.Thread(target=d.watchdog,args=(session,.12,.02));monitor.start()
            for i in range(10):
                d.atomic_json(session/'heartbeat.json',{'sequence':i,'phase':'long background download'});time.sleep(.03)
            d.atomic_json(session/'clean.json',{'clean':True});monitor.join(2)
            self.assertFalse(list(root.glob('incident-*.json')))

    def test_stack_sampling_has_no_locals_or_source_paths(self):
        local_password='DO_NOT_CAPTURE_LOCAL_VALUE'
        data=json.dumps(d.stacks())
        self.assertNotIn(local_password,data)
        self.assertNotIn(str(Path.cwd()),data)
        self.assertIn('test_stack_sampling',data)


if __name__=='__main__':unittest.main()
