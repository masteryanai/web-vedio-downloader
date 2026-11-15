import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import update_cache as cache
import preferences
import update_protocol as protocol

class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.install=self.base/'app';self.install.mkdir();self.exe=self.install/'WebVideoDownloader.exe';self.exe.write_bytes(b'app')
        self.patches=[patch.dict(os.environ,LOCALAPPDATA=str(self.base/'data')),patch('sys.frozen',True,create=True),patch('sys.executable',str(self.exe))]
        for p in self.patches:p.start()
        self.root=cache.register(cache.location())
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def package(self,index=1,size=100,used=None,root=None):
        path=(root or self.root)/('package-'+f'{index:064x}');path.mkdir(parents=True);(path/'package.zip').write_bytes(b'x'*size);cache.mark(path,'package')
        if used is not None:
            meta=cache.read_json(path/cache.ENTRY);meta['used_at']=used;cache.write_json(path/cache.ENTRY,meta)
        return path
    def test_default_and_custom_location_persistence(self):
        self.assertEqual(cache.location(),self.install/'update');new=self.base/'custom';cache.change_location(str(new));self.assertEqual(cache.location(),new)
        self.assertEqual(preferences.load()['update_cache_dir'],str(new));self.assertIn(self.root,cache.roots());cache.change_location('');self.assertEqual(cache.location(),self.root)
    def test_nonempty_unsafe_and_unwritable_locations_preserve_settings(self):
        other=self.base/'nonempty';other.mkdir();(other/'private.txt').write_text('private')
        for value in (str(other),str(self.install),str(self.install/'_internal'),str(cache.state_dir()),'relative'):
            with self.subTest(value=value),self.assertRaises((ValueError,OSError)):cache.change_location(value)
        with patch('update_cache.tempfile.TemporaryFile',side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(ValueError,'不可写'):cache.change_location(str(self.base/'denied'))
        self.assertEqual(preferences.load()['update_cache_dir'],'');self.assertEqual((other/'private.txt').read_text(),'private')
    def test_age_and_capacity_remove_oldest_idle_only(self):
        now=time.time();old=self.package(1,100,now-8*86400);a=self.package(2,100,now-100);b=self.package(3,100,now)
        result=cache.cleanup(now=now,limit=sum(p.stat().st_size for p in b.iterdir())+10);self.assertFalse(old.exists());self.assertFalse(a.exists());self.assertTrue(b.exists());self.assertGreaterEqual(result['freed'],200)
    def test_manual_cleanup_protects_prepared_stage_and_shared_package(self):
        package=self.package();stage=cache.new_stage(self.root);cache.mark(stage,'stage',archive=str(package/'package.zip'))
        cache.write_json(cache.state_dir()/'prepared.json',{'prepared':{'plan':str(stage/'plan.json')}})
        with self.assertRaisesRegex(ValueError,'待安装'):cache.change_location(str(self.base/'new'))
        result=cache.cleanup(manual=True);self.assertEqual(result['freed'],0);self.assertTrue(package.exists());self.assertTrue(stage.exists())
        (cache.state_dir()/'prepared.json').unlink();cache.cleanup(manual=True);self.assertFalse(stage.exists());self.assertFalse(package.exists())
    def test_corrupt_control_record_fails_closed(self):
        package=self.package();(cache.state_dir()/'pending.json').write_text('broken')
        cache.cleanup(manual=True);self.assertTrue(package.exists())
    def test_legacy_recognition_and_unknown_preservation(self):
        legacy=cache.state_dir();legacy.mkdir(exist_ok=True);release=legacy/'release-fixture';release.mkdir();(release/'package.zip').write_bytes(b'old')
        cache.write_json(release/'plan.json',{'install_dir':str(self.install),'version':'0.6.0','archive':str(release/'package.zip')})
        unknown=legacy/'install-unknown';unknown.mkdir();(unknown/'private').write_bytes(b'private');(self.root/'user.txt').write_bytes(b'note')
        cache.cleanup(manual=True);self.assertFalse(release.exists());self.assertTrue((unknown/'private').exists());self.assertTrue((self.root/'user.txt').exists());self.assertGreater(cache.summary()['unknown_count'],0)
    def test_legacy_old_install_cache_is_eligible_but_live_pid_is_protected(self):
        legacy=cache.state_dir();legacy.mkdir(exist_ok=True);release=legacy/'release-old-install';release.mkdir();(release/'package.zip').write_bytes(b'old')
        plan={'install_dir':str(self.base/'previous-install'),'version':'0.6.0','archive':str(release/'package.zip'),'pid':os.getpid()}
        cache.write_json(release/'plan.json',plan);cache.cleanup(manual=True);self.assertTrue(release.exists())
        plan['pid']=0;cache.write_json(release/'plan.json',plan);cache.cleanup(manual=True);self.assertFalse(release.exists())
    def test_unknown_file_inside_package_blocks_claim_and_deletion(self):
        package=self.package();(package/'private.txt').write_text('private')
        with self.assertRaises(ValueError):cache.package_dir(self.root,package.name[8:])
        cache.cleanup(manual=True);self.assertTrue((package/'private.txt').exists())
    def test_foreign_installation_entry_is_not_owned(self):
        package=self.package();meta=cache.read_json(package/cache.ENTRY);meta['install_dir']=str(self.base/'other');cache.write_json(package/cache.ENTRY,meta)
        cache.cleanup(manual=True);self.assertTrue(package.exists())
    def test_active_worker_blocks_cleanup_and_path_change(self):
        entered=threading.Event();release=threading.Event()
        def task():
            with cache.operation():entered.set();release.wait(3)
        thread=threading.Thread(target=task);thread.start();self.assertTrue(entered.wait(1))
        try:
            with self.assertRaisesRegex(RuntimeError,'正被使用'):cache.cleanup(manual=True)
            with self.assertRaises(RuntimeError):cache.change_location(str(self.base/'other'))
        finally:release.set();thread.join()
    def test_installer_exclusive_lock_blocks_maintenance(self):
        if os.name!='nt':self.skipTest('Windows installer lock')
        script="$f=[IO.File]::Open('"+str(cache.state_dir()/'writer.lock')+"',[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None); Write-Output 'locked'; Start-Sleep -Seconds 2; $f.Dispose()"
        process=subprocess.Popen(['powershell.exe','-NoProfile','-Command',script],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),'locked')
            with self.assertRaises(OSError):cache.cleanup(manual=True)
        finally:process.wait(5);process.stdout.close()
    def test_reuse_existing_complete_package_without_network(self):
        from update_transfer import download
        body=b'reusable verified archive';sha=hashlib.sha256(body).hexdigest();legacy=cache.state_dir()/('package-'+sha);legacy.mkdir(parents=True);(legacy/'package.zip').write_bytes(body)
        new=self.root/legacy.name;new.mkdir();cache.mark(new,'package');desc={'size':len(body),'sha256':sha};cache.reuse_package(new,desc)
        with patch('urllib.request.urlopen',side_effect=AssertionError('network must not run')):
            download(new/'package.zip',desc,lambda *_:self.fail('network invoked'),threading.Event(),lambda *_:None)
        self.assertEqual((new/'package.zip').read_bytes(),body)
    def test_confirmed_startup_removes_payload_and_archive_keeps_small_result(self):
        package=self.package();stage=cache.new_stage(self.root);cache.mark(stage,'stage',archive=str(package/'package.zip'));(stage/'payload').mkdir();(stage/'payload'/'file').write_bytes(b'payload')
        cache.write_json(stage/'plan.json',{'install_dir':str(self.install),'version':__import__('runtime').APP_VERSION});cache.write_json(stage/'result.json',{'ok':True});cache.write_json(cache.state_dir()/'last-install.json',{'stage':str(stage)})
        cache.maintain();self.assertFalse(package.exists());self.assertFalse((stage/'payload').exists());self.assertTrue((stage/'result.json').exists())
    def test_failed_stage_removed_but_resume_package_retained(self):
        package=self.package();stage=cache.new_stage(self.root);(stage/'payload').mkdir();cache.abort_stage();self.assertFalse(stage.exists());self.assertTrue(package.exists())
    def test_cache_never_enters_program_inventory(self):
        self.package();stage=cache.new_stage(self.root);(stage/'WebVideoDownloader.exe').write_bytes(b'prepared')
        inv=protocol.tree_inventory(self.install,'1.0.0');self.assertEqual([r['path'] for r in inv['files']],['WebVideoDownloader.exe'])
    @unittest.skipUnless(os.name=='nt','Windows junction')
    def test_junction_cache_cannot_delete_outside_root(self):
        secret=self.base/'secret';secret.mkdir();(secret/'keep.txt').write_text('keep');link=self.root/'install-link'
        command="New-Item -ItemType Junction -Path '"+str(link)+"' -Target '"+str(secret)+"' | Out-Null"
        subprocess.run(['powershell.exe','-NoProfile','-Command',command],check=True,capture_output=True)
        cache.cleanup(manual=True);self.assertEqual((secret/'keep.txt').read_text(),'keep')
        with self.assertRaises(ValueError):cache.change_location(str(link))

if __name__=='__main__':unittest.main()
