import contextlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import install_handoff as handoff
from update_protocol import digest


class Handoff(unittest.TestCase):
    def test_fresh_handshake_and_timeout_abort(self):
        with tempfile.TemporaryDirectory() as folder:
            stage=Path(folder);plan=stage/'plan.json';helper=stage/'update_window.ps1'
            helper.write_text('fixture');prepared={'plan':str(plan)}
            def launch(*args,**kwargs):
                config=json.loads(plan.read_text())
                handoff.atomic(stage/'window-ready.json',{'token':config['run_token'],'pid':123,'plan_sha256':digest(plan)})
                return type('Child',(),{'pid':123,'poll':lambda self:None})()
            with patch('update_cache.operation',contextlib.nullcontext),patch('update_pending.refresh_pid'),patch('install_handoff.resource_path',return_value=helper):
                plan.write_text(json.dumps({'ui_schema':1}))
                with patch('install_handoff.subprocess.Popen',side_effect=launch):
                    result=handoff.launch(prepared,timeout=.1)
                self.assertEqual(result['token'],json.loads(plan.read_text())['run_token'])
                old_token=result['token']
                child=type('Child',(),{'pid':123,'poll':lambda self:None})()
                with patch('install_handoff.subprocess.Popen',return_value=child),self.assertRaisesRegex(RuntimeError,'超时'):
                    handoff.launch(prepared,timeout=.01)
                config=json.loads(plan.read_text());self.assertNotEqual(config['run_token'],old_token)
                self.assertEqual(json.loads((stage/'handoff-abort.json').read_text())['token'],config['run_token'])
                self.assertFalse((stage/'window-ready.json').exists())

    def test_window_ack_requires_current_plan_version_and_visible_main(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stage=root/'update/install-test';stage.mkdir(parents=True);data=root/'data';(data/'updates').mkdir(parents=True)
            token='a'*32;plan=stage/'plan.json'
            plan.write_text(json.dumps({'run_token':token,'version':handoff.APP_VERSION,'install_dir':str(root)}))
            (data/'updates/last-install.json').write_text(json.dumps({'stage':str(stage)}))
            class Window:
                visible=False
                def isVisible(self):return self.visible
                def isMinimized(self):return False
                def windowHandle(self):return self
                def isExposed(self):return True
                def winId(self):return 42
            window=Window()
            with patch('install_handoff.program_dir',return_value=root),patch('install_handoff.app_data_dir',return_value=data),patch('update_cache.roots',return_value=[stage.parent]):
                self.assertFalse(handoff.acknowledge(window,plan,token))
                window.visible=True
                self.assertFalse(handoff.acknowledge(window,plan,'b'*32))
                self.assertTrue(handoff.acknowledge(window,plan,token))
                ack=json.loads((stage/'startup-ack.json').read_text());self.assertEqual(ack['pid'],os.getpid());self.assertEqual(ack['window'],42)
                plan.write_text(json.dumps({'run_token':token,'version':'0.0.0','install_dir':str(root)}))
                self.assertFalse(handoff.acknowledge(window,plan,token))
