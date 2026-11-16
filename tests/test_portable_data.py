import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import portable_data as data
import runtime

class PortableData(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.app=self.root/'app';self.app.mkdir();self.exe=self.app/'WebVideoDownloader.exe';self.exe.write_bytes(b'app')
        self.source=self.root/'local'/'WebVideoDownloaderPrototype';self.source.mkdir(parents=True);self.target=self.app/'data'
        self.patches=[patch.dict(os.environ,LOCALAPPDATA=str(self.root/'local'),WVD_ALLOW_TEST_PEERS='1'),patch('sys.frozen',True,create=True),patch('sys.executable',str(self.exe))]
        for p in self.patches:p.start()
        os.environ.pop('WVD_DATA_DIR',None)
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def write(self,name,body=b'value'):
        path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body);return path
    def test_default_data_root_and_deleted_update_cache_are_supported(self):
        self.write('settings.json',b'{"parallel_downloads":3}');self.write('download-tasks/history/task.json',b'{"output":"D:/media"}');self.write('diagnostics/log.txt',b'log')
        self.assertFalse((self.source/'updates').exists());self.assertEqual(runtime.app_data_dir(),self.target)
        result=data.initialize();self.assertEqual(result['migrated'],3);self.assertFalse(self.source.exists());self.assertEqual((self.target/'settings.json').read_bytes(),b'{"parallel_downloads":3}')
        self.assertEqual((self.target/'download-tasks/history/task.json').read_bytes(),b'{"output":"D:/media"}')
    def test_missing_entire_legacy_root_and_restart_do_not_recreate_it(self):
        self.source.rmdir();data.initialize();self.assertFalse(self.source.exists());data.initialize();self.assertFalse(self.source.exists())
    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_remembered_session_stays_encrypted_and_decrypts_same_user(self):
        from accounts import _dpapi,_storage_path
        original=_dpapi(b'fake-session-fixture');self.write('sessions.dpapi',original);data.initialize()
        self.assertEqual(_storage_path(),self.target/'sessions.dpapi');copied=_storage_path().read_bytes();self.assertEqual(copied,original);self.assertNotEqual(copied,b'fake-session-fixture');self.assertEqual(_dpapi(copied,decrypt=True),b'fake-session-fixture')
    @unittest.skipUnless(os.name=='nt','Windows remembered session')
    def test_account_manager_loads_migrated_remembered_session(self):
        from accounts import AccountManager,_dpapi
        payload={'version':1,'sites':{'B站':[{'name':'SESSDATA','value':'fixture-only','domain':'.bilibili.com','path':'/','expires':int(time.time())+3600}]}}
        self.write('sessions.dpapi',_dpapi(json.dumps(payload).encode()));data.initialize()
        manager=AccountManager();self.assertEqual(manager.cookies_for('B站')[0]['value'],'fixture-only');self.assertFalse(manager.last_error);manager.shutdown()
    def test_existing_target_configuration_wins_with_verified_conflict_copy(self):
        self.write('settings.json',b'old');self.target.mkdir();data.write(self.target/data.MARKER,{'application':'WebVideoDownloader','legacy_done':False,'install_dir':str(self.app)});(self.target/'settings.json').write_bytes(b'current')
        result=data.initialize();self.assertEqual(result['conflicts'],1);self.assertEqual((self.target/'settings.json').read_bytes(),b'current');self.assertEqual((self.target/'.migration-conflicts/settings.json').read_bytes(),b'old');self.assertFalse(self.source.exists());self.assertTrue(data.initialize()['already_initialized'])
    def test_cancel_and_copy_failure_keep_originals(self):
        self.write('settings.json',b'original');event=threading.Event()
        with self.assertRaisesRegex(RuntimeError,'取消'):data.migrate(self.source,self.target,event.is_set,lambda _:event.set())
        self.assertTrue((self.source/'settings.json').exists());self.assertFalse((self.target/'settings.json').exists())
        with patch('portable_data.shutil.copy2',side_effect=OSError('no space')),self.assertRaises(OSError):data.initialize()
        self.assertTrue((self.source/'settings.json').exists());self.assertFalse(data.read(self.target/data.MARKER).get('legacy_done'))
    def test_late_source_change_aborts_before_commit(self):
        self.write('settings.json',b'original')
        with self.assertRaisesRegex(RuntimeError,'变化'):data.migrate(self.source,self.target,progress=lambda _:self.write('late.json',b'late'))
        self.assertTrue((self.source/'settings.json').exists());self.assertFalse((self.target/'settings.json').exists())
    def test_corrupt_completed_marker_keeps_original_data(self):
        self.write('settings.json',b'original');self.target.mkdir();data.write(self.target/data.MARKER,{'application':'WebVideoDownloader','legacy_done':True})
        with self.assertRaisesRegex(ValueError,'标记损坏'):data.initialize()
        self.assertEqual((self.source/'settings.json').read_bytes(),b'original')
    def test_unowned_existing_data_directory_is_preserved(self):
        self.write('settings.json',b'original');self.target.mkdir();(self.target/'private.txt').write_bytes(b'private')
        with self.assertRaisesRegex(ValueError,'无法确认归属'):data.initialize()
        self.assertEqual((self.target/'private.txt').read_bytes(),b'private');self.assertTrue((self.source/'settings.json').exists())
    def test_readonly_destination_reports_error_without_system_drive_fallback(self):
        self.source.rmdir()
        with patch('portable_data.tempfile.TemporaryFile',side_effect=PermissionError('denied')),self.assertRaisesRegex(RuntimeError,'不会改用 C'):data.initialize()
        self.assertFalse(self.source.exists())
    def test_stale_missing_update_plan_is_archived_not_restored(self):
        self.write('updates/prepared.json',json.dumps({'prepared':{'plan':str(self.source/'updates/install-missing/plan.json'),'helper':'missing'}}).encode())
        self.write('settings.json',b'{}');data.initialize();self.assertFalse((self.target/'updates/prepared.json').exists());self.assertTrue((self.target/'updates/obsolete-prepared.json').exists())
    def test_old_unrecognized_update_payload_is_not_silently_deleted(self):
        self.write('settings.json',b'{}');self.write('updates/install-unknown/payload/keep.bin',b'keep')
        result=data.initialize();self.assertEqual(result['migrated'],1);self.assertEqual((self.source/'updates/install-unknown/payload/keep.bin').read_bytes(),b'keep');self.assertTrue((self.target/'settings.json').exists())
    def test_verified_target_is_required_before_any_original_is_removed(self):
        self.write('settings.json',b'original')
        actual=data.digest
        def bad(path):return 'bad' if '.migration-' in str(path) else actual(path)
        with patch('portable_data.digest',side_effect=bad),self.assertRaisesRegex(RuntimeError,'变化'):data.initialize()
        self.assertEqual((self.source/'settings.json').read_bytes(),b'original')
    def test_relocation_remaps_update_control_but_keeps_media_output_paths(self):
        self.source.rmdir();old=self.root/'old-app';self.target.mkdir()
        data.write(self.target/data.MARKER,{'application':'WebVideoDownloader','legacy_done':True,'install_dir':str(old)})
        data.write(self.target/'settings.json',{'update_cache_dir':str(old/'update'),'output':str(old/'downloads')})
        cache=self.app/'update';stage=cache/'install-fixture';(stage/'payload').mkdir(parents=True);(stage/'update_install.ps1').write_bytes(b'helper')
        data.write(cache/'.update-cache.json',{'application':'WebVideoDownloader','install_dir':str(old)})
        data.write(stage/'.cache-entry.json',{'application':'WebVideoDownloader','install_dir':str(old),'kind':'stage'})
        data.write(stage/'plan.json',{'install_dir':str(old),'data_dir':str(old/'data'),'payload_dir':str(old/'update/install-fixture/payload'),'from_version':runtime.APP_VERSION})
        data.write(self.target/'updates/prepared.json',{'prepared':{'plan':str(old/'update/install-fixture/plan.json'),'helper':str(old/'update/install-fixture/update_install.ps1')}})
        data.initialize();plan=data.read(stage/'plan.json');self.assertEqual(plan['install_dir'],str(self.app));self.assertEqual(plan['data_dir'],str(self.target))
        config=data.read(self.target/'settings.json');self.assertEqual(config['output'],str(old/'downloads'));self.assertEqual(config['update_cache_dir'],str(cache));self.assertTrue((self.target/'updates/prepared.json').exists())
    def test_data_and_cache_are_outside_program_inventory(self):
        import update_protocol
        self.target.mkdir();(self.target/'sessions.dpapi').write_bytes(b'not publishable');(self.app/'update').mkdir();(self.app/'update/package.zip').write_bytes(b'cache')
        inventory=update_protocol.tree_inventory(self.app,'1.0.0');self.assertEqual([row['path'] for row in inventory['files']],['WebVideoDownloader.exe'])
    @unittest.skipUnless(os.name=='nt','Windows junction')
    def test_linked_legacy_file_cannot_escape_migration(self):
        outside=self.root/'outside';outside.mkdir();(outside/'keep').write_text('keep');link=self.source/'linked'
        subprocess.run(['powershell.exe','-NoProfile','-Command',"New-Item -ItemType Junction -Path '"+str(link)+"' -Target '"+str(outside)+"' | Out-Null"],check=True,capture_output=True)
        with self.assertRaises(ValueError):data.initialize()
        self.assertEqual((outside/'keep').read_text(),'keep')

class MemoryMutex(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows mutex')
    def test_memory_lock_blocks_other_process_and_recovers_after_crash(self):
        from instance_identity import UserMutex,user_id
        self.assertTrue(user_id());name='WVD-test-'+__import__('uuid').uuid4().hex
        script="from instance_identity import UserMutex;import time; m=UserMutex('"+name+"');assert m.tryLock(0); print('locked',__import__('os').getpid(),flush=True); time.sleep(15)"
        child=subprocess.Popen([sys.executable,'-c',script],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        mutex=None
        try:
            line=child.stdout.readline().strip().split();self.assertEqual(line[0],'locked');owner=int(line[1]);mutex=UserMutex(name);self.assertFalse(mutex.tryLock(0));os.kill(owner,__import__('signal').SIGTERM);child.wait(5)
            deadline=time.monotonic()+1
            while not mutex.tryLock(0):
                self.assertLess(time.monotonic(),deadline);time.sleep(.01)
        finally:
            if mutex:mutex.close()
            if child.poll() is None:child.kill();child.wait()
            child.stdout.close();child.stderr.close()

if __name__=='__main__':unittest.main()
