import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
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
        write(self.install,'WebVideoDownloader.exe',b'old');write(self.install,'_internal/runtime.dll',b'fixed');write(self.install,'_internal/deleted.dat',b'deleted')
        write(self.new,'WebVideoDownloader.exe',b'new');write(self.new,'_internal/runtime.dll',b'fixed');write(self.new,'_internal/added.dat',b'added')
        self.base=protocol.tree_inventory(self.install,'0.8.0');self.target=protocol.tree_inventory(self.new,'0.9.0')
        write(self.install,protocol.INSTALLATION,protocol.canonical(self.base));self.cancel=threading.Event()
        self.env=patch.dict(os.environ,LOCALAPPDATA=str(self.root/'data'));self.env.start();self.current=patch('update_package.APP_VERSION','0.8.0');self.current.start()
    def tearDown(self):self.current.stop();self.env.stop();self.temp.cleanup()
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
        for name in ('../x','_internal/../x','_internal/CON.txt','_internal/x.','_internal/x:stream','downloads/video.mp4','_internal\\x'):
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
    def run_install(self,fail=0,launch=False):
        stage=self.root/('stage'+str(fail));stage.mkdir();payload=stage/'payload';shutil.copytree(self.new,payload)
        for name,data in [('base',self.base),('target',self.target)]:write(stage,name+'.json',protocol.canonical(data))
        data={'schema':2,'install_dir':str(self.install),'payload_dir':str(payload),'version':'0.9.0','from_version':'0.8.0',
            'pid':0,'operation':'upgrade','data_dir':str(self.root/'data'/'WebVideoDownloaderPrototype')}
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
        unknown=write(Path(index['folder']),'user-keep.txt',b'user backup note')
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'recovery.ps1'),'-InstallDir',str(self.install),'-Action','restore','-NoLaunch'],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertTrue(protocol.verify_tree(self.install,self.base)[0])
        self.assertEqual(unknown.read_bytes(),b'user backup note');self.assertFalse((unknown.parent/'backup-info.json').exists())
        self.assertEqual((self.install/'_internal/custom.txt').read_bytes(),b'private')
        self.assertTrue((self.root/'data'/'WebVideoDownloaderPrototype'/'updates'/'state.json').exists())
    @unittest.skipUnless(os.name=='nt','Windows installer')
    def test_transaction_failure_restores_every_touched_file(self):
        result,report=self.run_install(fail=2);self.assertEqual(result.returncode,1);self.assertFalse(report['ok']);self.assertFalse(report['restore_error']);self.assertIn('Injected',report['error'])
        self.assertTrue(protocol.verify_tree(self.install,self.base)[0]);self.assertFalse((self.install/'_internal/added.dat').exists())
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

    def test_polling_backoff_ignore_and_snooze_do_not_erase_known_release(self):
        state={};update_state.succeeded(state,{'available':True,'version':'99.0.0'},now=100)
        self.assertTrue(update_state.visible(state));update_state.failed(state,now=200)
        self.assertEqual(state['next_retry'],1100);self.assertTrue(update_state.visible(state))
        self.assertFalse(update_state.due(state,True,4,1000));self.assertTrue(update_state.due(state,True,4,15000))
        state['ignored_version']='99.0.0';self.assertFalse(update_state.visible(state))
        update_state.succeeded(state,{'available':True,'version':'99.0.1'},now=200);self.assertTrue(update_state.visible(state))


if __name__=='__main__':unittest.main()
