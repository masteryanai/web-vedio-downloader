"""Product boundaries are checked in fresh processes, like separate EXEs."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class ProductIdentity(unittest.TestCase):
    def run_product(self,product,code):
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,WVD_PRODUCT=product,WVD_DATA_DIR=str(Path(folder)/'data'))
            return subprocess.run([sys.executable,'-X','utf8','-c',code],cwd=ROOT,env=env,capture_output=True,text=True)

    def test_lite_rejects_manager_update_and_inventory(self):
        result=self.run_product('lite',"""
from runtime import PRODUCT_ID,EXECUTABLE,UPDATE_MANIFEST
from updater import validate_manifest
from update_protocol import validate_inventory
assert PRODUCT_ID=='WebVideoDownloaderLite'
assert EXECUTABLE=='WebVideoDownloaderLite.exe'
assert UPDATE_MANIFEST=='update-lite.json'
for check,value in [(lambda v:validate_manifest(v,'v0.17.0'),{'application':'WebVideoDownloader','version':'0.17.0'}),
                    (validate_inventory,{'application':'WebVideoDownloader','schema':2,'version':'0.17.0','files':[]})]:
 try:check(value)
 except (ValueError,RuntimeError):pass
 else:raise AssertionError('cross-product update accepted')
""")
        self.assertEqual(result.returncode,0,result.stderr)

    def test_profile_marker_cannot_be_reused_by_other_product(self):
        result=self.run_product('lite',"""
import json
from runtime import app_data_dir,program_dir
from portable_data import initialize
root=app_data_dir();root.mkdir();(root/'.portable-data.json').write_text(json.dumps({'application':'WebVideoDownloader','legacy_done':True,'install_dir':str(program_dir())}))
try:initialize()
except ValueError:pass
else:raise AssertionError('manager profile accepted by lite')
assert json.loads((root/'.portable-data.json').read_text())['application']=='WebVideoDownloader'
""")
        self.assertEqual(result.returncode,0,result.stderr)
