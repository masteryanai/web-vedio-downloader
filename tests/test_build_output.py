import json
import marshal
import os
from pathlib import Path
import tempfile
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import zipfile
import package_release
import trim_bundle
import verify_bundle
from build_guard import check_output

class BuildOutput(unittest.TestCase):
    def test_helpers_use_selected_bundle_and_leave_old_output_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=root/'dist/WebVideoDownloader';old.mkdir(parents=True)
            sentinel=old/'data/library.json';sentinel.parent.mkdir();sentinel.write_bytes(b'old private catalogue')
            selected=root/'build/release-output/WebVideoDownloader';internal=selected/'_internal';internal.mkdir(parents=True)
            (root/'main.py').write_text('print("fixture")')
            code=compile('print("fixture")','main.py','exec',dont_inherit=True)
            class Archive:
                toc={'PYZ.pyz':None}
                def open_embedded_archive(self,name):return type('PYZ',(),{'toc':{}})()
                def extract(self,name):return marshal.dumps(code)
            for name in ('update_install.ps1','update_window.ps1','recovery.ps1'):
                (root/name).write_bytes(b'fixture');(internal/name).write_bytes(b'fixture')
            debug=internal/'PySide6/resources/qtwebengine_devtools_resources.debug.pak';debug.parent.mkdir(parents=True);debug.write_bytes(b'debug')
            trim_bundle.trim(selected);self.assertFalse(debug.exists())
            with patch('verify_bundle.CArchiveReader',return_value=Archive()) as reader:
                verify_bundle.verify(root,selected)
                self.assertEqual(reader.call_args.args[0],str(selected/'WebVideoDownloader.exe'))
            with self.assertRaises(RuntimeError):check_output(old)
            self.assertEqual(sentinel.read_bytes(),b'old private catalogue')

    def test_production_package_uses_only_safe_output_and_explicit_source_is_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=root/'dist/WebVideoDownloader';old.mkdir(parents=True)
            (old/'WebVideoDownloader.exe').write_bytes(b'old exe');sentinel=old/'data/library.json';sentinel.parent.mkdir();sentinel.write_bytes(b'private original')
            (root/'release_policy.json').write_text(json.dumps({'data_schema':1,'force_full':False,'force_full_reason':'','revoked_versions':[]}))
            with patch('package_release.__file__',str(root/'package_release.py')),patch('release_history.ensure_history'):
                with self.assertRaisesRegex(RuntimeError,'build.ps1'):package_release.package()
                self.assertFalse((root/'release').exists())
                source=root/'build/release-output/WebVideoDownloader';(source/'_internal').mkdir(parents=True)
                (source/'WebVideoDownloader.exe').write_bytes(b'new exe')
                release=package_release.package()
                with zipfile.ZipFile(next(release.glob('*windows-x64.zip'))) as archive:
                    self.assertEqual(archive.read('WebVideoDownloader/WebVideoDownloader.exe'),b'new exe')
            with self.assertRaises(RuntimeError):package_release.package(root,source=old)
            self.assertEqual(sentinel.read_bytes(),b'private original')

    def test_stable_runtime_is_never_a_build_target(self):
        with tempfile.TemporaryDirectory() as folder,patch('build_guard.STABLE_RUNTIME',folder):
            with self.assertRaises(RuntimeError):check_output(folder)

    @unittest.skipUnless(os.name=='nt','PowerShell build entry point')
    def test_build_default_routes_all_helpers_to_safe_output_and_rejects_legacy_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);repo=Path(__file__).resolve().parents[1]
            for name in ('build.ps1','build_guard.py'):shutil.copy2(repo/name,root/name)
            for name in ('tools/ffmpeg.exe','tools/ffprobe.exe','tools/node.exe','vendor/cctv-h5e-decrypt/build/src/cli/main.js','README.md','LICENSES.md'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
            (root/'site_registry.py').write_text('')
            for name in ('trim_bundle.py','verify_bundle.py'):
                (root/name).write_text("import sys;from pathlib import Path;Path(__file__).with_suffix('.called').write_text(sys.argv[1])")
            module=root/'PyInstaller';module.mkdir();(module/'__init__.py').write_text('')
            (module/'__main__.py').write_text("import sys;from pathlib import Path;p=Path(sys.argv[sys.argv.index('--distpath')+1])/'WebVideoDownloader';p.mkdir(parents=True);(p/'WebVideoDownloader.exe').write_bytes(b'new exe')")
            old=root/'dist/WebVideoDownloader/data/library.json';old.parent.mkdir(parents=True);old.write_bytes(b'old library')
            command=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(root/'build.ps1'),'-PythonPath',sys.executable]
            result=subprocess.run(command,capture_output=True,text=True,errors="replace",timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            selected=root/'build/release-output/WebVideoDownloader'
            self.assertEqual((selected/'WebVideoDownloader.exe').read_bytes(),b'new exe')
            for name in ('trim_bundle.called','verify_bundle.called'):self.assertEqual((root/name).read_text(),str(selected))
            result=subprocess.run(command+['-DistPath',str(root/'dist')],capture_output=True,text=True,errors="replace",timeout=30)
            self.assertNotEqual(result.returncode,0);self.assertEqual(old.read_bytes(),b'old library')
