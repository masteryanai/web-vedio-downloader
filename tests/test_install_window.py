"""Exercise the real Windows Forms installer and native startup handshake."""
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from update_protocol import canonical,digest,tree_inventory,verify_tree

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name=='nt','Native Windows installer')
class InstallWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler=tempfile.TemporaryDirectory();folder=Path(cls.compiler.name)
        source=folder/'fixture.cs'
        source.write_text(r'''
using System;using System.IO;using System.Diagnostics;using System.Reflection;using System.Windows.Forms;using System.Web.Script.Serialization;
[assembly: AssemblyVersion("0.9.0.0")]
[assembly: AssemblyFileVersion("0.9.0.0")]
[assembly: AssemblyInformationalVersion("0.9.0")]
class Fixture {
 [STAThread] static void Main(string[] args) {
  Form form=new Form();form.Text="Web Video Downloader · 0.9.0";
  form.Shown+=(s,e)=>{
   if(args.Length==4){
    string mode=File.ReadAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"startup-mode.txt"));
    if(mode=="none")return;
    var ack=new {token=mode=="wrong"?"bad":args[3],version="0.9.0",install_dir=AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\'),pid=Process.GetCurrentProcess().Id,window=form.Handle.ToInt64()};
    File.WriteAllText(Path.Combine(Path.GetDirectoryName(args[1]),"startup-ack.json"),new JavaScriptSerializer().Serialize(ack));
   }
  };Application.Run(form);
 }
}''',encoding='utf-8')
        cls.exe=folder/'WebVideoDownloader.exe'
        compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        result=subprocess.run([str(compiler),'/nologo','/target:winexe','/r:System.Windows.Forms.dll','/r:System.Web.Extensions.dll','/out:'+str(cls.exe),str(source)],capture_output=True,text=True)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)
        cls.u=ctypes.WinDLL('user32');cls.callback=ctypes.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
        cls.u.EnumWindows.argtypes=[cls.callback,W.LPARAM];cls.u.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
        cls.u.IsWindowVisible.argtypes=[W.HWND];cls.u.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];cls.u.ShowWindow.argtypes=[W.HWND,ctypes.c_int];cls.u.IsIconic.argtypes=[W.HWND]
        cls.u.EnumChildWindows.argtypes=[W.HWND,cls.callback,W.LPARAM];cls.u.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,ctypes.c_int]

    @classmethod
    def tearDownClass(cls):cls.compiler.cleanup()

    def wait(self,predicate,timeout=20):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            value=predicate()
            if value:return value
            time.sleep(.05)
        self.fail('Native installer wait timed out')

    def visible(self,pid):
        handles=[]
        @self.callback
        def visit(handle,_):
            owner=W.DWORD();self.u.GetWindowThreadProcessId(handle,ctypes.byref(owner))
            if owner.value==pid and self.u.IsWindowVisible(handle):handles.append(handle)
            return True
        self.u.EnumWindows(visit,0);return handles

    def run_case(self,mode='ok',capability=True):
        with tempfile.TemporaryDirectory(prefix='wvd-install-window-') as folder:
            root=Path(folder);install=root/'app';install.mkdir();(install/'WebVideoDownloader.exe').write_bytes(b'original fixture')
            stage=install/'update/install-test';payload=stage/'payload';payload.mkdir(parents=True)
            shutil.copy2(self.exe,payload/self.exe.name);(install/'startup-mode.txt').write_text(mode)
            base=tree_inventory(install,'0.8.0');target=tree_inventory(payload,'0.9.0')
            if capability:target['startup_confirmation']=1
            for name,value in [('base',base),('target',target)]: (stage/(name+'.json')).write_bytes(canonical(value))
            gate=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 60'],creationflags=subprocess.CREATE_NO_WINDOW)
            token=uuid.uuid4().hex
            config={'schema':2,'ui_schema':1,'run_token':token,'install_dir':str(install),'payload_dir':str(payload),'version':'0.9.0','from_version':'0.8.0','pid':gate.pid,'operation':'upgrade','data_dir':str(install/'data'),'startup_timeout':2}
            for name in ('target','base'):config[name+'_inventory']=str(stage/(name+'.json'));config[name+'_sha256']=digest(stage/(name+'.json'))
            (stage/'plan.json').write_bytes(canonical(config))
            for name in ('update_window.ps1','update_install.ps1'):shutil.copy2(ROOT/name,stage/name)
            ui=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-STA','-ExecutionPolicy','Bypass','-File',str(stage/'update_window.ps1'),'-Plan',str(stage/'plan.json')],stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                self.wait(lambda:(stage/'window-ready.json').exists() or ui.poll() is not None)
                self.assertIsNone(ui.poll(),ui.communicate() if ui.poll() is not None else '')
                ready=json.loads((stage/'window-ready.json').read_text());self.assertEqual(ready['token'],token);self.assertEqual(ready['pid'],ui.pid);self.assertEqual(ready['plan_sha256'],digest(stage/'plan.json'))
                handle=ready['window'];self.assertTrue(self.u.IsWindowVisible(handle))
                self.u.PostMessageW(handle,0x10,0,0);time.sleep(.2);self.assertIsNone(ui.poll())
                self.u.ShowWindow(handle,6);self.wait(lambda:self.u.IsIconic(handle));self.u.ShowWindow(handle,9)
                self.wait(lambda:(stage/'progress.json').exists())
                self.assertEqual((install/'WebVideoDownloader.exe').read_bytes(),b'original fixture')
                gate.terminate();gate.wait(5)
                result=self.wait(lambda:self.read_result(stage))
                self.wait(lambda:not self.process_alive(result['installer_pid']))
                if mode=='ok':
                    self.assertTrue(result['ok'],result);self.wait(lambda:ui.poll() is not None);self.assertEqual(ui.returncode,0)
                    self.assertTrue(verify_tree(install,target)[0]);self.assertTrue(result['startup_confirmed'])
                    self.assertGreater(result['stats']['reused_hashes'],0);self.assertIn('confirm_startup',result['timings'])
                else:
                    self.assertFalse(result['ok']);self.assertEqual(result['failed_phase'],'confirm_startup');self.assertEqual(result['rollback_state'],'restored')
                    self.assertTrue(verify_tree(install,base)[0]);self.assertIsNone(ui.poll());self.assertTrue(self.u.IsWindowVisible(handle))
                    self.wait(lambda:'更新未完成' in self.child_texts(handle))
                    self.u.PostMessageW(handle,0x10,0,0);self.wait(lambda:ui.poll() is not None);self.assertEqual(ui.returncode,1)
                self.assertTrue((stage/'install.log').exists())
            finally:
                if gate.poll() is None:gate.terminate();gate.wait(5)
                if ui.poll() is None:ui.terminate();ui.wait(5)
                ui.communicate()
                # Kill only a fixture process whose executable is in this temporary directory.
                script="$p=Get-Process WebVideoDownloader -ErrorAction SilentlyContinue | Where-Object {$_.Path -eq '"+str(install/'WebVideoDownloader.exe')+"'}; $p | Stop-Process -Force"
                subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True)
                time.sleep(.2)

    def child_texts(self,handle):
        texts=[]
        @self.callback
        def visit(child,_):
            text=ctypes.create_unicode_buffer(4096);self.u.GetWindowTextW(child,text,len(text));texts.append(text.value);return True
        self.u.EnumChildWindows(handle,visit,0);return texts

    @staticmethod
    def read_result(stage):
        try:return json.loads((stage/'result.json').read_text(encoding='utf-8-sig'))
        except (OSError,ValueError):return None

    @staticmethod
    def process_alive(pid):
        k=ctypes.WinDLL('kernel32');k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=W.HANDLE;k.CloseHandle.argtypes=[W.HANDLE]
        handle=k.OpenProcess(0x1000,False,pid)
        if handle:
            k.GetExitCodeProcess.argtypes=[W.HANDLE,ctypes.POINTER(W.DWORD)];code=W.DWORD()
            try:k.GetExitCodeProcess(handle,ctypes.byref(code));return code.value==259
            finally:k.CloseHandle(handle)
        return False

    def test_success_visible_handoff_minimize_and_confirmed_startup(self):self.run_case()
    def test_wrong_ack_rolls_back_and_keeps_failure_window(self):self.run_case('wrong')
    def test_older_target_uses_visible_window_version_confirmation(self):self.run_case(capability=False)
