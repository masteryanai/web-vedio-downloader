import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from media_library import Catalogue

class Recovery(unittest.TestCase):
 def test_index_loss_and_moved_library_restore_identity_settings_media(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c=Catalogue();lib=c.library(str(root/'media'))
   wid,eids=c.ingest({'site':'demo','key':'w','url':'https://example.org/work/1','entries':[{'key':'1','title':'one','url':'https://example.org/episode/1'}]},'Title','Season',lib)
   media=c.output(wid)/'one.mp4';media.write_bytes(b'original');c.attach(eids[0],media);c.patch_work(wid,no_play_prompt=True)
   c.close();c.path.unlink();(root/'media').rename(root/'moved')
   fresh=Catalogue();preview=fresh.probe_library(root/'moved');self.assertEqual(preview['works'],1)
   self.assertEqual(fresh.mount_library(root/'moved'),lib)
   self.assertTrue(fresh.work(wid)['no_play_prompt']);self.assertEqual(Path(fresh.episode(eids[0])['files'][0]['path']).read_bytes(),b'original');fresh.close()

 def fixture(self,root):
  c=Catalogue();lib=c.library(str(root/'media'));wid,eids=c.ingest({'site':'demo','key':'w','url':'https://example.org/work/1?token=private&id=1','entries':[{'key':'1','title':'one','url':'https://example.org/episode/1?auth=private'}]},'Title','Season',lib)
  media=c.output(wid)/'one.mp4';media.write_bytes(b'original');c.attach(eids[0],media)
  return c,lib,wid,eids,media

 def test_detach_keeps_archive_remove_work_does_not_resurrect(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root)
   c.remove_library(lib);self.assertEqual(c.read()['works'],[]);c.mount_library(root/'media');self.assertEqual(c.work(wid)['id'],wid)
   c.remove(wid);c.remove_library(lib);c.mount_library(root/'media');self.assertEqual(c.read()['works'],[]);self.assertEqual(media.read_bytes(),b'original');c.close()

 def test_copy_requires_choice_clone_has_independent_identity(self):
  import shutil
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);shutil.copytree(root/'media',root/'copy')
   with self.assertRaises(ValueError):c.mount_library(root/'copy')
   clone=c.mount_library(root/'copy',mode='clone');self.assertNotEqual(clone,lib)
   copied=next(w for w in c.read()['works'] if w['library_id']==clone);self.assertNotEqual(copied['id'],wid)
   c.patch_work(copied['id'],no_play_prompt=True);self.assertFalse(c.work(wid)['no_play_prompt']);c.close()

 def test_corruption_history_sources_and_unknown_format_preserve_media(self):
  import json
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root)
   for hours in (2,3,4,5):c.patch_work(wid,check_hours=hours)
   preview=c.probe_library(root/'media');self.assertEqual(len(preview['histories']),3)
   current=root/'media/.wvd-library/current.json';current.write_text('{broken',encoding='utf-8')
   self.assertEqual(c.probe_library(root/'media')['state'],'corrupt')
   with self.assertRaises(ValueError):c.patch_work(wid,check_hours=9)
   c.mount_library(root/'media',recovery='history:'+preview['histories'][0]['id']);self.assertEqual(c.work(wid)['check_hours'],4)
   current.write_text(json.dumps({'format':99}),encoding='utf-8');self.assertEqual(c.probe_library(root/'media')['state'],'unknown')
   self.assertTrue(c.probe_library(root/'media')['sources_available']);c.mount_library(root/'media',recovery='sources')
   self.assertEqual(c.library_status(lib)['state'],'needs_online_confirmation');self.assertEqual(media.read_bytes(),b'original');c.close()

 def test_exclusive_writer_blocks_mutation_but_can_mount_and_read(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'first')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root)
   with patch.dict(os.environ,WVD_DATA_DIR=str(root/'second')):
    second=Catalogue();second.mount_library(root/'media');self.assertEqual(second.work(wid)['title'],'Title')
    with self.assertRaises(ValueError):second.patch_work(wid,no_play_prompt=True)
    self.assertFalse(second.work(wid)['no_play_prompt']);c.close();self.assertTrue(second.library_status(lib)['writable']);second.patch_work(wid,no_play_prompt=True);second.close()

 def test_covers_live_with_library_and_sources_exclude_credentials(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);cover=root/'cover.png';cover.write_bytes(b'fake-image')
   c.patch_work(wid,cover=str(cover),banner=str(cover));c.close();c.path.unlink();cover.unlink()
   fresh=Catalogue();fresh.mount_library(root/'media');work=fresh.work(wid);self.assertEqual(Path(work['cover']).read_bytes(),b'fake-image');self.assertEqual(work['cover'],work['banner']);self.assertNotIn('private',str(work['sources']));fresh.close()

 def test_media_survives_failed_metadata_commit_and_retry(self):
  from media_library import MetadataSaveError
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);other=media.parent/'two.mp4';other.write_bytes(b'finished')
   replace=os.replace
   def failing(source,target):
    if Path(target).name=='current.json':raise PermissionError('simulated disk write failure')
    return replace(source,target)
   with patch('os.replace',side_effect=failing):
    with self.assertRaises(MetadataSaveError):c.attach(eids[0],other)
   self.assertEqual(other.read_bytes(),b'finished');self.assertTrue(c.library_status(lib)['pending']);self.assertTrue(any(f['path']==str(other) for f in c.episode(eids[0])['files']))
   self.assertTrue(c.retry_persist(lib));self.assertFalse(c.library_status(lib)['pending']);c.close()

 def test_another_process_read_only_and_abnormal_exit_releases_writer(self):
  import subprocess,sys
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);c.close()
   env=dict(os.environ,WVD_DATA_DIR=str(root/'child'))
   script="import os,sys;from media_library import Catalogue;c=Catalogue();key=c.mount_library(sys.argv[1]);print(c.library_status(key)['writable'],flush=True);sys.stdin.readline();os._exit(7)"
   child=subprocess.Popen([sys.executable,'-X','utf8','-c',script,str(root/'media')],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   try:
    self.assertEqual(child.stdout.readline().strip(),'True');self.assertFalse(c.library_status(lib)['writable'])
    with self.assertRaises(ValueError):c.patch_work(wid,no_play_prompt=True)
    child.communicate('\n',timeout=10);self.assertEqual(child.returncode,7);self.assertTrue(c.library_status(lib)['writable']);c.patch_work(wid,no_play_prompt=True)
   finally:
    if child.poll() is None:child.kill();child.communicate()
    c.close()

 def test_freeze_blocks_other_catalogue_writes_without_blocking_reads(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);second=Catalogue()
   self.assertTrue(second.library_status(lib)['writable'])
   with c.freeze_library(lib):
    self.assertEqual(second.work(wid)['title'],'Title')
    with self.assertRaises(ValueError):second.patch_work(wid,no_play_prompt=True)
    with self.assertRaises(ValueError):second.delete_owned(eids[0],[str(media)])
    self.assertTrue(media.exists())
   second.patch_work(wid,no_play_prompt=True);self.assertTrue(c.work(wid)['no_play_prompt']);second.close();self.assertTrue(c.library_status(lib)['writable']);c.close()
