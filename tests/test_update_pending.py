import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import update_pending
import update_bootstrap
import update_protocol as protocol
from runtime import APP_VERSION,app_data_dir

ROOT=Path(__file__).resolve().parents[1]


class PendingUpdate(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.env=patch.dict(os.environ,LOCALAPPDATA=str(self.root/'data'),WVD_DATA_DIR=str(self.root/'data'/'WebVideoDownloaderPrototype'));self.env.start()
        self.install=self.root/'app';self.install.mkdir();self.exe=self.install/'WebVideoDownloader.exe';self.exe.write_bytes(b'old')
        self.stage=app_data_dir()/'updates/install-fixture';self.payload=self.stage/'payload';self.payload.mkdir(parents=True)
        (self.payload/'WebVideoDownloader.exe').write_bytes(b'new')
        base=protocol.tree_inventory(self.install,APP_VERSION);target=protocol.tree_inventory(self.payload,'99.0.0')
        self.config={'schema':2,'install_dir':str(self.install),'payload_dir':str(self.payload),'version':'99.0.0','from_version':APP_VERSION,'pid':12345}
        for name,inventory in [('base',base),('target',target)]:
            file=self.stage/(name+'.json');file.write_bytes(protocol.canonical(inventory));self.config[name+'_inventory']=str(file);self.config[name+'_sha256']=protocol.digest(file)
        self.plan=self.stage/'plan.json';self.plan.write_bytes(protocol.canonical(self.config));helper=self.stage/'update_install.ps1';shutil.copy2(ROOT/'update_install.ps1',helper)
        self.prepared={'plan':str(self.plan),'helper':str(helper)};self.info={'version':'99.0.0','available':True,'notes':'# update'}
        self.patches=[patch('sys.frozen',True,create=True),patch('sys.executable',str(self.exe))]
        for item in self.patches:item.start()
    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.env.stop();self.temp.cleanup()
    def test_ready_plan_survives_restart_and_refreshes_owner_pid(self):
        update_pending.remember(self.prepared,self.info);restored=update_pending.restore()
        self.assertEqual(restored['info']['version'],'99.0.0');self.assertEqual(restored['prepared'],self.prepared)
        update_pending.refresh_pid(self.prepared);self.assertEqual(json.loads(self.plan.read_text(encoding='utf-8'))['pid'],os.getpid())
    def test_stale_or_modified_metadata_is_not_restored(self):
        update_pending.remember(self.prepared,self.info)
        with patch('update_pending.APP_VERSION','1.2.3'):self.assertIsNone(update_pending.restore())
        (self.stage/'target.json').write_text('{}');self.assertIsNone(update_pending.restore())
    def test_installer_failure_keeps_ready_plan_and_explicit_error(self):
        update_pending.remember(self.prepared,self.info)
        (self.stage/'result.json').write_text(json.dumps({'ok':False,'error':'Application still running'}))
        self.assertIn('still running',update_pending.restore()['error'])
    def test_new_process_checks_version_and_acknowledges_success_once(self):
        update_pending.remember(self.prepared,self.info)
        updates=app_data_dir()/'updates';(updates/'last-install.json').write_text(json.dumps({'stage':str(self.stage)}))
        (self.stage/'result.json').write_text(json.dumps({'ok':True,'version':'99.0.0'}))
        with patch('update_bootstrap.APP_VERSION','99.0.0'):
            notice=update_bootstrap.result_notice();self.assertTrue(notice['ok']);self.assertIn('99.0.0',notice['message'])
            self.assertIsNone(update_bootstrap.result_notice())
        self.assertFalse(update_pending.pointer().exists())
    def test_success_record_with_old_running_version_is_reported_as_mismatch(self):
        updates=app_data_dir()/'updates';(updates/'last-install.json').write_text(json.dumps({'stage':str(self.stage)}));(self.stage/'result.json').write_text('{"ok":true}')
        notice=update_bootstrap.result_notice();self.assertFalse(notice['ok']);self.assertIn(APP_VERSION,notice['message'])


if __name__=='__main__':unittest.main()
