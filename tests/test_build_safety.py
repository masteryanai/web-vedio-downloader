import runpy
import os
import subprocess
from unittest.mock import patch
from build_guard import check_output
import package_release
import tempfile
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class BuildSafety(unittest.TestCase):
    def test_direct_spec_stops_before_analysis_and_preserves_user_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            dist=Path(folder);app=dist/'WebVideoDownloader';sentinel=app/'data/library.json'
            sentinel.parent.mkdir(parents=True);sentinel.write_bytes(b'original library')
            with self.assertRaisesRegex(RuntimeError,'用户|user'):
                runpy.run_path(str(ROOT/'WebVideoDownloader.spec'),init_globals={'SPECPATH':str(ROOT),'DISTPATH':str(dist)})
            self.assertEqual(sentinel.read_bytes(),b'original library')

    def test_preflight_preserves_metadata_tasks_media_and_unknown_files(self):
        for name in ('data/index.json', '.wvd-library/manifest.json', 'movie.mp4', 'unknown/sentinel.bin', '_internal/.wvd-library/history.json'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);file=root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'private original')
                with self.assertRaises(RuntimeError):check_output(root)
                self.assertEqual(file.read_bytes(),b'private original')

    def test_packaging_stops_before_writing_release_for_private_internal_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'dist/WebVideoDownloader';file=source/'_internal/.wvd-library/manifest.json'
            file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'private original')
            with self.assertRaises(RuntimeError):package_release.package(root)
            self.assertFalse((root/'release').exists());self.assertEqual(file.read_bytes(),b'private original')

    @unittest.skipUnless(os.name=='nt','Windows junction')
    def test_junction_is_rejected_without_reading_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);output=root/'app';output.mkdir();target=root/'private';target.mkdir()
            sentinel=target/'sentinel';sentinel.write_bytes(b'untouched')
            junction=output/'_internal'
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',f"New-Item -ItemType Junction -Path '{junction}' -Target '{target}' | Out-Null"],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            original_scandir=os.scandir
            def scan(path):
                self.assertEqual(Path(path),output);return original_scandir(path)
            try:
                with patch('build_guard.os.scandir',side_effect=scan),self.assertRaises(RuntimeError):check_output(output)
                self.assertEqual(sentinel.read_bytes(),b'untouched')
            finally:junction.rmdir()

    def test_second_preflight_stops_user_state_created_during_analysis(self):
        with tempfile.TemporaryDirectory() as folder:
            dist=Path(folder);sentinel=dist/'WebVideoDownloader/data/tasks.json'
            def analyze(*args,**kwargs):
                sentinel.parent.mkdir(parents=True);sentinel.write_bytes(b'new task during analysis')
                return type('AnalysisResult',(),{'binaries':[],'datas':[],'pure':[],'scripts':[]})()
            def collect(*args,**kwargs):self.fail('COLLECT reached protected output')
            bindings={'SPECPATH':str(ROOT),'DISTPATH':str(dist),'Analysis':analyze,'PYZ':lambda *a,**k:None,'EXE':lambda *a,**k:None,'COLLECT':collect}
            with patch('PyInstaller.utils.hooks.collect_submodules',return_value=[]),self.assertRaises(RuntimeError):
                runpy.run_path(str(ROOT/'WebVideoDownloader.spec'),init_globals=bindings)
            self.assertEqual(sentinel.read_bytes(),b'new task during analysis')
