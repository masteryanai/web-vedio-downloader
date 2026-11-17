import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile
import update_protocol as protocol
import update_package as packages
import update_state

ROOT=Path(__file__).resolve().parents[1]


def write(root,name,body):
    file=root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(body);return file


class UpdateProtocol(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.install=self.root/'app';self.new=self.root/'new'
        write(self.install,'WebVideoDownloader.exe',b'old');write(self.install,'_internal/runtime.dll',b'fixed'*200000);write(self.install,'_internal/deleted.dat',b'deleted')
        write(self.new,'WebVideoDownloader.exe',b'new');write(self.new,'_internal/runtime.dll',b'fixed'*200000);write(self.new,'_internal/added.dat',b'added')
        self.base=protocol.tree_inventory(self.install,'0.8.0');self.target=protocol.tree_inventory(self.new,'0.9.0')
        write(self.install,protocol.INSTALLATION,protocol.canonical(self.base));self.cancel=threading.Event()
        self.user_sentinels={name:(b'{"sentinel":true}' if name.endswith('.json') else ('private:'+name).encode()) for name in ('data/library.json','data/settings.json','data/tasks.json','data/covers/cover.jpg','.wvd-library/manifest.json','.wvd-library/history/previous.json','media/episode.mp4','media/subtitle.srt')}
        for name,body in self.user_sentinels.items():write(self.install,name,body)
        self.env=patch.dict(os.environ,LOCALAPPDATA=str(self.root/'data'),WVD_DATA_DIR=str(self.root/'data'/'WebVideoDownloaderPrototype'));self.env.start();self.current=patch('update_package.APP_VERSION','0.8.0');self.current.start();self.pending_version=patch('update_pending.APP_VERSION','0.8.0');self.pending_version.start()
    def tearDown(self):
        for name,body in self.user_sentinels.items():self.assertEqual((self.install/name).read_bytes(),body,name)
        self.pending_version.stop();self.current.stop();self.env.stop()
        for attempt in range(30):
            try:self.temp.cleanup();break
            except PermissionError:
                if attempt==29:raise
                time.sleep(.05) # The restored C# fixture may still be finishing process shutdown.

    def archive(self,changed=None):
        path=self.root/'package.zip'
        with zipfile.ZipFile(path,'w') as output:
            for row in self.target['files']:
                if changed is None or row['path'] in changed:output.write(self.new/row['path'],'WebVideoDownloader/'+row['path'])
        return path
    def test_delta_assembles_exact_complete_target_and_deletes_only_owned(self):
        changed,deleted=protocol.differences(self.base,self.target);self.assertEqual(deleted,['_internal/deleted.dat'])
        write(self.install,'_internal/my-notes.txt',b'private');payload=self.root/'payload'
        protocol.assemble_delta(self.install,self.archive(changed),payload,self.base,self.target,self.cancel)
        self.assertTrue(protocol.verify_tree(payload,self.target)[0]);self.assertFalse((payload/'_internal/deleted.dat').exists())
        self.assertEqual((self.install/'_internal/my-notes.txt').read_bytes(),b'private')
    def test_paths_duplicates_symlinks_and_missing_members_rejected(self):
        for name in ('../x','_internal/../x','_internal/CON.txt','_internal/x.','_internal/x:stream','downloads/video.mp4','_internal/data/index.json','_internal/catalogue.json','_internal/sessions.dpapi','_internal/download-tasks/job.json','_internal/.wvd-library/manifest.json','_internal/movie.mp4','_internal\\x'):
            with self.subTest(name=name),self.assertRaises(ValueError):protocol.relative(name)
        inv=json.loads(protocol.canonical(self.target));inv['files'].append(inv['files'][0]);inv['inventory_sha256']=hashlib.sha256(protocol.canonical(inv['files'])).hexdigest()
        with self.assertRaises(ValueError):protocol.validate_inventory(inv)
        with self.assertRaises(ValueError):protocol.extract_verified(self.archive([]),self.root/'bad',self.target)
    @unittest.skipUnless(os.name=='nt','Windows junction validation')
    def test_junction_cannot_redirect_program_file_operations(self):
        target=self.root/'private';target.mkdir();write(target,'secret.txt',b'secret')
        junction=self.install/'_internal'/'junction'
        script=f"New-Item -ItemType Junction -Path '{junction}' -Target '{target}' | Out-Null"
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True)
        if result.returncode:self.skipTest('junction creation unavailable')
        with self.assertRaises(ValueError):protocol.owned_path(self.install,'_internal/junction/secret.txt')
        self.assertEqual((target/'secret.txt').read_bytes(),b'secret')

    def info(self):
        desc={'asset':'full.zip','tag':'v0.9.0','sha256':'a'*64,'size':100}
        return {'version':'0.9.0','manifest':{**desc,'schema':2,'inventory':{},'deltas':[{'from_version':'0.8.0','base_inventory':{},'deleted':['_internal/deleted.dat'],**desc}]}}
    def test_selection_verifies_baseline_and_requires_explicit_full_fallback(self):
        info=self.info()
        with patch('update_package.fetch_json',side_effect=[self.target,self.base]):selected=packages.select(info,self.install)
        self.assertEqual(selected['mode'],'delta')
        write(self.install,'WebVideoDownloader.exe',b'modified')
        with patch('update_package.fetch_json',side_effect=[self.target,self.base]),self.assertRaises(packages.FullConfirmation):packages.select(info,self.install)
        with patch('update_package.fetch_json',side_effect=[self.target,self.base]):self.assertEqual(packages.select(info,self.install,allow_full=True)['mode'],'full')
    def test_forced_full_does_not_need_fallback_prompt(self):
        info=self.info();info['manifest'].update(force_full=True,force_full_reason='runtime changed')
        with patch('update_package.fetch_json',return_value=self.target):result=packages.select(info,self.install)
        self.assertEqual(result['mode'],'full');self.assertEqual(result['reason'],'runtime changed')
    def test_metadata_hash_is_checked_before_json(self):
        desc={'asset':'inventory.json','tag':'v0.9.0','sha256':'a'*64,'size':2}
        with patch('updater.request',return_value=io.BytesIO(b'{}')),self.assertRaises(ValueError):packages.fetch_json(desc)
    def run_install(self,fail=0,launch=False,operation="upgrade"):
        stage=self.root/('stage'+str(fail));stage.mkdir();payload=stage/'payload';shutil.copytree(self.new,payload)
        for name,data in [('base',self.base),('target',self.target)]:write(stage,name+'.json',protocol.canonical(data))
        data={'schema':2,'install_dir':str(self.install),'payload_dir':str(payload),'version':'0.9.0','from_version':'0.8.0',
            'pid':0,'operation':operation,'data_dir':str(self.root/'data'/'WebVideoDownloaderPrototype')}
        for name in ('target','base'):data[name+'_inventory']=str(stage/(name+'.json'));data[name+'_sha256']=protocol.digest(stage/(name+'.json'))
        write(stage,'plan.json',protocol.canonical(data))
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'update_install.ps1'),'-Plan',str(stage/'plan.json'),* ([] if launch else ['-NoLaunch']),'-FailAfter',str(fail)],capture_output=True,text=True,timeout=30)
        report=json.loads((stage/'result.json').read_text(encoding='utf-8-sig'))
        return result,report
    @unittest.skipUnless(os.name=='nt','Windows installer')
    def test_transaction_installs_deletes_preserves_userdata_and_offline_recovery(self):
        write(self.install,'_internal/custom.txt',b'private');write(self.root/'data'/'WebVideoDownloaderPrototype','settings.json',b'{"parallel_downloads":3}')
        result,report=self.run_install();self.assertEqual(result.returncode,0,report)
        self.assertTrue(protocol.verify_tree(self.install,self.target)[0]);self.assertFalse((self.install/'_internal/deleted.dat').exists())
        self.assertEqual((self.install/'_internal/custom.txt').read_bytes(),b'private')
        index=packages.backup_info(self.install);self.assertIsNotNone(index)
        self.assertFalse((Path(index['folder'])/'_internal/runtime.dll').exists())
        self.assertFalse((Path(index['folder'])/'settings-before-update.json').exists())
        marker=json.loads((Path(index['folder'])/'backup-info.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(marker['format'],'partial-v1')
        unknown=write(Path(index['folder']),'user-keep.txt',b'user backup note')
        unowned=write(Path(index['folder']),'_internal/runtime.dll',b'unowned backup sentinel')
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'recovery.ps1'),'-InstallDir',str(self.install),'-Action','restore','-NoLaunch'],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertTrue(protocol.verify_tree(self.install,self.base)[0])
        state=self.root/'data'/'WebVideoDownloaderPrototype'/'updates'
        self.assertEqual(Path(json.loads((state/'last-install.json').read_text(encoding='utf-8-sig'))['stage']).parent,self.install/'update')
        self.assertEqual(unowned.read_bytes(),b'unowned backup sentinel')
        self.assertEqual(unknown.read_bytes(),b'user backup note');self.assertFalse((unknown.parent/'backup-info.json').exists())
        self.assertEqual((self.install/'_internal/custom.txt').read_bytes(),b'private')
        self.assertTrue((self.root/'data'/'WebVideoDownloaderPrototype'/'updates'/'state.json').exists())
    @unittest.skipUnless(os.name=='nt','Windows recovery')
    def test_independent_recovery_uses_configured_cache_directory(self):
        result,report=self.run_install();self.assertEqual(result.returncode,0,report)
        custom=self.root/'custom-update';data=self.root/'data'/'WebVideoDownloaderPrototype'
        write(data,'settings.json',json.dumps({'update_cache_dir':str(custom)}).encode())
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'recovery.ps1'),'-InstallDir',str(self.install),'-Action','restore','-NoLaunch'],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(protocol.verify_tree(self.install,self.base)[0])
        last=json.loads((data/'updates'/'last-install.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(Path(last['stage']).parent,custom)
        self.assertTrue((custom/'.update-cache.json').exists())

    @unittest.skipUnless(os.name=='nt','Windows portable recovery')
    def test_recovery_default_uses_program_data_without_legacy_writes(self):
        result,report=self.run_install();self.assertEqual(result.returncode,0,report)
        old=self.root/'data'/'WebVideoDownloaderPrototype';portable=self.install/'data'
        (portable/'updates').mkdir(parents=True);(portable/'.portable-data.json').write_text(json.dumps({'application':'WebVideoDownloader','legacy_done':True,'install_dir':str(self.install)}));shutil.copy2(old/'updates/backup.json',portable/'updates/backup.json')
        before={p.relative_to(old).as_posix():protocol.digest(p) for p in old.rglob('*') if p.is_file()}
        env=dict(os.environ);env.pop('WVD_DATA_DIR',None)
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'recovery.ps1'),'-InstallDir',str(self.install),'-Action','restore','-NoLaunch'],env=env,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertTrue((portable/'updates/last-install.json').exists())
        after={p.relative_to(old).as_posix():protocol.digest(p) for p in old.rglob('*') if p.is_file()};self.assertEqual(before,after)

    @unittest.skipUnless(os.name=='nt','Windows local backup')
    def test_partial_backup_local_payload_reuses_verified_install_and_rejects_unknown_format(self):
        result,report=self.run_install();self.assertEqual(result.returncode,0,report)
        info={'version':'0.8.0','operation':'rollback','rollback_allowed':True,'local_restore':True,'manifest':{}}
        with patch('update_package.APP_VERSION','0.9.0'),patch('update_pending.APP_VERSION','0.9.0'),patch('sys.frozen',True,create=True),patch('sys.executable',str(self.install/'WebVideoDownloader.exe')):
            prepared=packages.prepare(info,self.cancel,lambda _:None)
        plan=json.loads(Path(prepared['plan']).read_text(encoding='utf-8'))
        self.assertTrue(protocol.verify_tree(plan['payload_dir'],self.base)[0])
        markerfile=Path(report['backup'])/'backup-info.json'
        marker=json.loads(markerfile.read_text(encoding='utf-8-sig'));marker['format']='future';markerfile.write_text(json.dumps(marker))
        self.assertIsNone(packages.backup_info(self.install))
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'recovery.ps1'),'-InstallDir',str(self.install),'-Action','restore','-NoLaunch'],capture_output=True,text=True,timeout=30)
        self.assertNotEqual(result.returncode,0)
        self.assertTrue(protocol.verify_tree(self.install,self.target)[0])
        marker['format']='partial-v1';markerfile.write_text(json.dumps(marker))
        plan['pid']=0;Path(prepared['plan']).write_bytes(protocol.canonical(plan))
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'update_install.ps1'),'-Plan',prepared['plan'],'-NoLaunch'],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(protocol.verify_tree(self.install,self.base)[0])

    @unittest.skipUnless(os.name=='nt','Windows partial restore verification')
    def test_partial_backup_rejects_damaged_unchanged_dll_for_both_restore_paths(self):
        result,report=self.run_install();self.assertEqual(result.returncode,0,report)
        write(self.install,'_internal/runtime.dll',b'damaged current DLL')
        backup=packages.backup_info(self.install)
        with self.assertRaises(ValueError):packages.backup_sources(backup)
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'recovery.ps1'),'-InstallDir',str(self.install),'-Action','restore','-NoLaunch'],capture_output=True,text=True,timeout=30)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((self.install/'WebVideoDownloader.exe').read_bytes(),b'new')
        self.assertEqual((self.install/'_internal/runtime.dll').read_bytes(),b'damaged current DLL')

    @unittest.skipUnless(os.name=='nt','Windows legacy full restore')
    def test_legacy_full_backup_still_restores_offline(self):
        result,report=self.run_install();self.assertEqual(result.returncode,0,report)
        folder=Path(report['backup']);shutil.copy2(self.install/'_internal/runtime.dll',folder/'_internal/runtime.dll')
        markerfile=folder/'backup-info.json';marker=json.loads(markerfile.read_text(encoding='utf-8-sig'))
        for key in ('format','owned_files','inventory_sha256'):marker.pop(key)
        markerfile.write_text(json.dumps(marker))
        backup=packages.backup_info(self.install);self.assertIsNotNone(backup)
        self.assertEqual(len(packages.backup_sources(backup)),len(self.base['files']))
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'recovery.ps1'),'-InstallDir',str(self.install),'-Action','restore','-NoLaunch'],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(protocol.verify_tree(self.install,self.base)[0])

    @unittest.skipUnless(os.name=='nt','Windows original bytes restore')
    def test_damaged_actual_original_is_backed_up_and_failure_restores_it(self):
        write(self.install,'_internal/runtime.dll',b'actual damaged original')
        result,report=self.run_install(fail=2);self.assertEqual(result.returncode,1,report)
        self.assertEqual((self.install/'_internal/runtime.dll').read_bytes(),b'actual damaged original')
        self.assertEqual((Path(report['backup'])/'_internal/runtime.dll').read_bytes(),b'actual damaged original')
        self.assertEqual((self.install/'WebVideoDownloader.exe').read_bytes(),b'old')

    @unittest.skipUnless(os.name=='nt','Windows owned backup cleanup')
    def test_compact_cleanup_keeps_unowned_and_changed_owned_files(self):
        result,report=self.run_install();self.assertEqual(result.returncode,0,report)
        folder=Path(report['backup'])
        unknown=write(folder,'_internal/runtime.dll',b'unowned backup sentinel')
        changed=write(folder,'WebVideoDownloader.exe',b'user changed backup')
        result,second=self.run_install(fail=-1);self.assertEqual(result.returncode,0,second)
        self.assertEqual(unknown.read_bytes(),b'unowned backup sentinel')
        self.assertEqual(changed.read_bytes(),b'user changed backup')
        self.assertTrue((folder/'backup-info.json').exists())

    @unittest.skipUnless(os.name=='nt','Windows compatibility')
    def test_incompatible_old_layout_backup_is_not_offered(self):
        result,report=self.run_install();self.assertEqual(result.returncode,0,report)
        self.assertIsNotNone(packages.backup_info(self.install))
        current=json.loads((self.install/protocol.INSTALLATION).read_text(encoding='utf-8-sig'));current['data_schema']=2
        (self.install/protocol.INSTALLATION).write_bytes(protocol.canonical(current))
        self.assertIsNone(packages.backup_info(self.install))

    @unittest.skipUnless(os.name=='nt','Windows installer')
    def test_transaction_failure_restores_every_touched_file(self):
        result,report=self.run_install(fail=2);self.assertEqual(result.returncode,1);self.assertFalse(report['ok']);self.assertFalse(report['restore_error']);self.assertIn('Injected',report['error'])
        self.assertTrue(protocol.verify_tree(self.install,self.base)[0]);self.assertFalse((self.install/'_internal/added.dat').exists())
        self.assertFalse((Path(report['backup'])/'_internal/runtime.dll').exists())
    @unittest.skipUnless(os.name=='nt','Windows repair')
    def test_repair_preserves_all_user_sentinels(self):
        result,report=self.run_install(operation='repair');self.assertEqual(result.returncode,0,report)
        self.assertTrue(protocol.verify_tree(self.install,self.target)[0])

    @unittest.skipUnless(os.name=='nt','Windows repair')
    def test_failed_repair_restores_program_and_preserves_all_user_sentinels(self):
        result,report=self.run_install(fail=2,operation='repair');self.assertEqual(result.returncode,1,report)
        self.assertTrue(protocol.verify_tree(self.install,self.base)[0])

    @unittest.skipUnless(os.name=='nt','Windows rollback')
    def test_failed_rollback_restores_program_and_preserves_all_user_sentinels(self):
        result,report=self.run_install(fail=2,operation='rollback');self.assertEqual(result.returncode,1,report)
        self.assertTrue(protocol.verify_tree(self.install,self.base)[0])

    @unittest.skipUnless(os.name=='nt','Windows startup rollback')
    def test_failed_target_process_restores_previous_program_and_backup_index(self):
        compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.exists():self.skipTest('C# fixture compiler unavailable')
        for folder,code in ((self.install,0),(self.new,3)):
            source=self.root/f'fixture{code}.cs';source.write_text(f'public class Program {{ public static void Main() {{ System.Environment.Exit({code}); }} }}')
            result=subprocess.run([str(compiler),'/nologo','/target:winexe','/out:'+str(folder/'WebVideoDownloader.exe'),str(source)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.base=protocol.tree_inventory(self.install,'0.8.0');self.target=protocol.tree_inventory(self.new,'0.9.0')
        write(self.install,protocol.INSTALLATION,protocol.canonical(self.base))
        result,report=self.run_install(launch=True)
        self.assertEqual(result.returncode,1,report);self.assertIn('failed to start',report['error'])
        self.assertTrue(protocol.verify_tree(self.install,self.base)[0]);self.assertFalse(report['restore_error'])

    @unittest.skipUnless(os.name=='nt','Windows install lock')
    def test_competing_installer_cannot_modify_program(self):
        with (self.install/'.WebVideoDownloader-update.lock').open('a+b'):
            result,report=self.run_install()
        self.assertEqual(result.returncode,1);self.assertFalse(report['ok']);self.assertIsNone(report['backup'])
        self.assertTrue(protocol.verify_tree(self.install,self.base)[0])

    def test_schema2_prepare_downloads_delta_and_never_mutates_installed_program(self):
        info=self.info();changed,_=protocol.differences(self.base,self.target);archive=self.archive(changed);body=archive.read_bytes()
        delta=info['manifest']['deltas'][0];delta.update(size=len(body),sha256=hashlib.sha256(body).hexdigest())
        class Response(io.BytesIO):
            headers={'Content-Length':str(len(body))}
            def getcode(self):return 200
        with patch('update_package.fetch_json',side_effect=[self.target,self.base]),patch('updater.request',return_value=Response(body)),patch('sys.frozen',True,create=True),patch('sys.executable',str(self.install/'WebVideoDownloader.exe')):
            prepared=packages.prepare(info,self.cancel,lambda _:None)
        plan=json.loads(Path(prepared['plan']).read_text(encoding='utf-8'))
        self.assertTrue(protocol.verify_tree(plan['payload_dir'],self.target)[0]);self.assertTrue(protocol.verify_tree(self.install,self.base)[0])
        self.assertTrue(Path(prepared['helper']).is_file())
        import update_pending
        with patch('sys.frozen',True,create=True),patch('sys.executable',str(self.install/'WebVideoDownloader.exe')):
            self.assertEqual(update_pending.restore()['prepared']['plan'],prepared['plan'])

    def test_preview_reads_no_program_bytes_or_remote_inventories(self):
        with patch('update_package.verify_tree',side_effect=AssertionError('preview hashed files')),patch('update_package.fetch_json',side_effect=AssertionError('preview fetched inventory')):
            estimate,confirm=packages.preview(self.info())
        self.assertEqual(estimate['mode'],'delta');self.assertFalse(confirm)

    def test_prepare_hashes_baseline_once_and_target_once(self):
        info=self.info();changed,_=protocol.differences(self.base,self.target);body=self.archive(changed).read_bytes()
        info['manifest']['deltas'][0].update(size=len(body),sha256=hashlib.sha256(body).hexdigest())
        class Response(io.BytesIO):
            headers={'Content-Length':str(len(body))}
            def getcode(self):return 200
        with patch('update_package.fetch_json',side_effect=[self.target,self.base]),patch('updater.request',return_value=Response(body)),patch('sys.frozen',True,create=True),patch('sys.executable',str(self.install/'WebVideoDownloader.exe')),patch('update_package.verify_tree',wraps=protocol.verify_tree) as verify,patch('update_protocol.verify_tree',side_effect=AssertionError('duplicate assembly scan')):
            packages.prepare(info,self.cancel,lambda _:None)
        self.assertEqual(verify.call_count,2)
        self.assertEqual(Path(verify.call_args_list[0].args[0]),self.install)
        self.assertEqual(Path(verify.call_args_list[1].args[0]).name,'payload')

    def test_source_change_after_baseline_check_is_detected_by_final_target_hash(self):
        info=self.info();changed,_=protocol.differences(self.base,self.target);body=self.archive(changed).read_bytes()
        info['manifest']['deltas'][0].update(size=len(body),sha256=hashlib.sha256(body).hexdigest())
        class Response(io.BytesIO):
            headers={'Content-Length':str(len(body))}
            def getcode(self):return 200
        def response(*args,**kwargs):
            write(self.install,'_internal/runtime.dll',b'changed after preflight')
            return Response(body)
        with patch('update_package.fetch_json',side_effect=[self.target,self.base]),patch('updater.request',side_effect=response),patch('sys.frozen',True,create=True),patch('sys.executable',str(self.install/'WebVideoDownloader.exe')),self.assertRaises(packages.FullConfirmation):
            packages.prepare(info,self.cancel,lambda _:None)

    def test_polling_backoff_ignore_and_snooze_do_not_erase_known_release(self):
        state={};update_state.succeeded(state,{'available':True,'version':'99.0.0'},now=100)
        self.assertTrue(update_state.visible(state));update_state.failed(state,now=200)
        self.assertEqual(state['next_retry'],1100);self.assertTrue(update_state.visible(state))
        self.assertFalse(update_state.due(state,True,4,1000));self.assertTrue(update_state.due(state,True,4,15000))
        state['ignored_version']='99.0.0';self.assertFalse(update_state.visible(state))
        update_state.succeeded(state,{'available':True,'version':'99.0.1'},now=200);self.assertTrue(update_state.visible(state))


if __name__=='__main__':unittest.main()
