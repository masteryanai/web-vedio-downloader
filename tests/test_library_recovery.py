import os,tempfile,unittest,json
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

 def test_read_only_mount_never_restores_tasks_and_clone_rejects_before_handler(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'first')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root)
   with patch.dict(os.environ,WVD_DATA_DIR=str(root/'second')):
    other=Catalogue();called=[];other.task_restore_handler=lambda *args:called.append(args)
    other.mount_library(root/'media');self.assertEqual(called,[])
    other.remove_library(lib)
    with self.assertRaises(ValueError):other.mount_library(root/'media',mode='clone')
    self.assertEqual(called,[]);other.close()
   c.close()

 def test_source_confirmation_preserves_bad_original_and_reenables_writes(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);current=root/'media/.wvd-library/current.json';bad=b'{broken original';current.write_bytes(bad)
   c.mount_library(root/'media',recovery='sources');self.assertEqual(c.library_status(lib)['state'],'needs_online_confirmation')
   self.assertTrue(c.confirm_source_restore(lib));self.assertTrue(c.library_status(lib)['writable']);c.patch_work(wid,check_hours=7)
   saved=list((current.parent/'quarantine').glob('*.json'));self.assertTrue(any(p.read_bytes()==bad for p in saved));self.assertEqual(c.probe_library(root/'media')['state'],'valid');c.close()

 def test_history_confirmation_keeps_bad_bytes_and_unknown_format_is_protected(self):
  import json
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);c.patch_work(wid,check_hours=8);history=c.probe_library(root/'media')['histories'][0]['id'];current=root/'media/.wvd-library/current.json';bad=b'broken history original';current.write_bytes(bad)
   c.mount_library(root/'media',recovery='history:'+history);c.patch_work(wid,check_hours=9)
   self.assertTrue(any(p.read_bytes()==bad for p in (current.parent/'quarantine').glob('*.json')))
   future=json.dumps({'format':99}).encode();current.write_bytes(future);c.mount_library(root/'media',recovery='sources')
   with self.assertRaises(ValueError):c.confirm_source_restore(lib)
   self.assertEqual(current.read_bytes(),future);c.close()

 def test_clone_task_archive_can_be_detached_and_restored_again(self):
  import shutil,download_tasks as tasks
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root)
   task=tasks.create({'url':'https://example.org/episode/1','site':'demo','title':'one'},'hd',media.parent);task['library_episode']=eids[0];tasks.save(task)
   cache=tasks.work_path(task);cache.mkdir(parents=True);(cache/'source.part').write_bytes(b'partial');c.bind(eids[0],task['id'])
   shutil.copytree(root/'media',root/'clone');clib=c.mount_library(root/'clone',mode='clone');cw=next(w for w in c.read()['works'] if w['library_id']==clib);ep=next(e for e in c.read()['episodes'] if e['work_id']==cw['id']);ctask=ep['task_id']
   self.assertNotEqual(ctask,task['id']);self.assertEqual(tasks.work_path(tasks.load(ctask)).joinpath('source.part').read_bytes(),b'partial')
   c.remove_library(clib);c.mount_library(root/'clone');ep=c.episode(ep['id']);self.assertEqual(ep['task_id'],ctask);self.assertEqual(tasks.work_path(tasks.load(ctask)).joinpath('source.part').read_bytes(),b'partial');self.assertEqual(tasks.load(task['id'])['output'],str(media.parent));c.close()

 def test_confirmed_central_index_rebuild_preserves_original_then_mount_restores(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);c.patch_work(wid,no_play_prompt=True);c.close();bad=b'{broken central index';c.path.write_bytes(bad)
   with self.assertRaises(ValueError):c.read()
   saved=c.rebuild_index();self.assertEqual(Path(saved).read_bytes(),bad);self.assertEqual(c.read()['works'],[])
   c.mount_library(root/'media');self.assertEqual(c.work(wid)['id'],wid);self.assertTrue(c.work(wid)['no_play_prompt']);self.assertEqual(Path(c.episode(eids[0])['files'][0]['path']).read_bytes(),b'original');c.close()

 def test_rebuild_index_never_replaces_future_schema(self):
  import json
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   c=Catalogue();c.path.parent.mkdir(parents=True);future=json.dumps({'schema':3,'libraries':[],'works':[],'episodes':[]}).encode();c.path.write_bytes(future)
   with self.assertRaises(ValueError):c.rebuild_index()
   self.assertEqual(c.path.read_bytes(),future);self.assertEqual(list((c.path.parent/'quarantine').glob('*')),[]);c.close()

 def test_source_confirmation_gate_and_metadata_warning_survive_restart(self):
  from media_library import MetadataSaveError
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);current=root/'media/.wvd-library/current.json';current.write_bytes(b'broken');c.mount_library(root/'media',recovery='sources');c.close()
   fresh=Catalogue();self.assertFalse(fresh.library_status(lib)['writable']);self.assertEqual(fresh.library_status(lib)['state'],'needs_online_confirmation');fresh.confirm_source_restore(lib)
   replace=os.replace
   def fail(source,target):
    if Path(target).name=='current.json':raise PermissionError('disk')
    return replace(source,target)
   with patch('os.replace',side_effect=fail):
    with self.assertRaises(MetadataSaveError):fresh.patch_work(wid,check_hours=8)
   fresh.close();again=Catalogue();self.assertTrue(again.library_status(lib)['pending']);again.retry_persist(lib);again.close();last=Catalogue();self.assertFalse(last.library_status(lib)['pending']);last.close()

 def test_clone_archive_failure_rolls_back_index_tasks_caches_and_target_lease(self):
  import shutil,download_tasks as tasks
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);task=tasks.create({'url':'https://example.org/episode/1','site':'demo','title':'one'},'hd',media.parent);task['library_episode']=eids[0];tasks.save(task);cache=tasks.work_path(task);cache.mkdir(parents=True);(cache/'source.part').write_bytes(b'partial');c.bind(eids[0],task['id']);shutil.copytree(root/'media',root/'copy')
   before=c.path.read_bytes();taskbefore=tasks.task_file(task['id']).read_bytes();replace=os.replace
   def fail(source,target):
    if Path(target)==root/'copy/.wvd-library/current.json':raise PermissionError('disk')
    return replace(source,target)
   with patch('os.replace',side_effect=fail):
    with self.assertRaises(OSError):c.mount_library(root/'copy',mode='clone')
   self.assertEqual(c.path.read_bytes(),before);self.assertEqual(tasks.task_file(task['id']).read_bytes(),taskbefore);self.assertEqual({r['id'] for r in tasks.records()},{task['id']});self.assertEqual(len(list((root/'copy').rglob('.video-downloader-*'))),1)
   with patch.dict(os.environ,WVD_DATA_DIR=str(root/'second')):
    other=Catalogue();other.mount_library(root/'copy');self.assertTrue(other.library_status(lib)['writable']);other.close()
   self.assertTrue(c.library_status(lib)['writable']);c.close()

 def test_relocation_central_failure_rolls_back_task_output_index_and_old_lease(self):
  import shutil,download_tasks as tasks
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);task=tasks.create({'url':'https://example.org/episode/1','site':'demo','title':'one'},'hd',media.parent);task['library_episode']=eids[0];tasks.save(task);c.bind(eids[0],task['id']);shutil.copytree(root/'media',root/'moved');before=c.path.read_bytes();taskbefore=tasks.task_file(task['id']).read_bytes();replace=os.replace
   def fail(source,target):
    if Path(target)==c.path:raise PermissionError('central disk')
    return replace(source,target)
   with patch('os.replace',side_effect=fail):
    with self.assertRaises(OSError):c.mount_library(root/'moved',mode='relocate')
   self.assertEqual(c.path.read_bytes(),before);self.assertEqual(tasks.task_file(task['id']).read_bytes(),taskbefore);self.assertEqual(tasks.load(task['id'])['output'],str(media.parent));self.assertTrue(c.library_status(lib)['writable'])
   with patch.dict(os.environ,WVD_DATA_DIR=str(root/'second')):
    other=Catalogue();other.mount_library(root/'moved');self.assertTrue(other.library_status(lib)['writable']);other.close()
   c.close()

 @unittest.skipUnless(os.name=='nt','Windows junction boundary')
 def test_windows_junction_library_and_file_references_are_rejected(self):
  import subprocess
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);alias=root/'alias'
   made=subprocess.run(['cmd','/c','mklink','/J',str(alias),str(media.parent)],capture_output=True)
   self.assertEqual(made.returncode,0)
   with self.assertRaises(ValueError):c.probe_library(alias)
   with self.assertRaises(ValueError):c.attach(eids[0],alias/media.name)
   self.assertEqual(media.read_bytes(),b'original');c.close()

 def test_missing_task_bridge_does_not_drop_unbound_resume_tasks(self):
  import builtins,download_tasks as tasks
  from media_library import MetadataSaveError
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);task=tasks.create({'url':'https://example.org/episode/1','site':'demo','title':'one'},'hd',media.parent);original=builtins.__import__
   def missing(name,*args,**kwargs):
    if name=='library_tasks':raise ImportError('missing bridge')
    return original(name,*args,**kwargs)
   with patch('builtins.__import__',side_effect=missing):
    with self.assertRaises(MetadataSaveError):c.patch_work(wid,check_hours=12)
   self.assertTrue(c.library_status(lib)['pending']);self.assertEqual(tasks.load(task['id'])['output'],str(media.parent));c.close()

 @unittest.skipUnless(os.name=='nt','Windows sharing publication boundary')
 def test_library_publish_waits_for_brief_external_reader(self):
  import threading
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);current=root/'media/.wvd-library/current.json';errors=[]
   held=current.open('rb')
   def write():
    try:c.patch_work(wid,check_hours=15)
    except BaseException as exc:errors.append(exc)
   thread=threading.Thread(target=write);thread.start();threading.Event().wait(.05);held.close();thread.join(5)
   self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(c.work(wid)['check_hours'],15);c.close()

 def test_library_load_reader_and_publication_share_io_gate(self):
  import threading
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);current=root/'media/.wvd-library/current.json';opened=threading.Event();release=threading.Event();done=threading.Event();errors=[];original=Path.open
   def held(path,*args,**kwargs):
    handle=original(path,*args,**kwargs)
    if path==current and threading.current_thread().name=='archive-reader':opened.set();release.wait(3)
    return handle
   def reader():
    try:c.probe_library(root/'media')
    except BaseException as exc:errors.append(exc)
   def writer():
    try:c.patch_work(wid,check_hours=17)
    except BaseException as exc:errors.append(exc)
    finally:done.set()
   with patch.object(Path,'open',held):
    first=threading.Thread(target=reader,name='archive-reader');first.start();self.assertTrue(opened.wait(3));second=threading.Thread(target=writer);second.start()
    try:self.assertFalse(done.wait(.25))
    finally:release.set();first.join(5);second.join(5)
   self.assertEqual(errors,[]);self.assertTrue(done.is_set());c.close()

 def test_nested_storage_roots_are_rejected_for_creation_mount_and_preview(self):
  import shutil
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);nested=root/'media/another'
   with self.assertRaises(ValueError):c.library(nested)
   with self.assertRaises(ValueError):c.library(root)
   # A genuine, independently identified existing library cannot be nested either.
   other=c.library(root/'independent');c.remove_library(other);shutil.copytree(root/'independent',nested)
   self.assertIn('嵌套',c.probe_library(nested)['error'])
   with self.assertRaises(ValueError):c.mount_library(nested)
   self.assertEqual([v['id'] for v in c.read()['libraries']],[lib]);c.close()

 def test_metadata_damage_is_read_only_and_pending_retry_repairs_only_known_format(self):
  import json
  from media_library import MetadataSaveError
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);current=root/'media/.wvd-library/current.json';current.write_bytes(b'broken')
   self.assertFalse(c.library_status(lib)['writable']);self.assertEqual(c.library_status(lib)['state'],'corrupt')
   current.write_text(json.dumps({'format':99}),encoding='utf-8');self.assertEqual(c.library_status(lib)['state'],'unknown');future=current.read_bytes()
   with self.assertRaises(ValueError):c.retry_persist(lib)
   self.assertEqual(current.read_bytes(),future);c.close()

 def test_pending_metadata_retry_can_quarantine_corruption_but_never_future_format(self):
  import json
  from media_library import MetadataSaveError
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c,lib,wid,eids,media=self.fixture(root);current=root/'media/.wvd-library/current.json';replace=os.replace
   def fail(source,target):
    if Path(target)==current:raise PermissionError('disk')
    return replace(source,target)
   with patch('os.replace',side_effect=fail):
    with self.assertRaises(MetadataSaveError):c.patch_work(wid,check_hours=20)
   bad=b'corrupt after failed publication';current.write_bytes(bad);self.assertFalse(c.library_status(lib)['writable']);self.assertTrue(c.library_status(lib)['pending']);self.assertTrue(c.retry_persist(lib));self.assertEqual(c.work(wid)['check_hours'],20);self.assertTrue(any(p.read_bytes()==bad for p in (current.parent/'quarantine').glob('*.json')))
   with patch('os.replace',side_effect=fail):
    with self.assertRaises(MetadataSaveError):c.patch_work(wid,check_hours=21)
   future=json.dumps({'format':100}).encode();current.write_bytes(future)
   with self.assertRaises(ValueError):c.retry_persist(lib)
   self.assertEqual(current.read_bytes(),future);c.close()

 def test_portable_sources_strip_unrecognized_credentials_even_from_url_identity_keys(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,WVD_DATA_DIR=str(Path(tmp)/'data')):
   root=Path(tmp);c=Catalogue();lib=c.library(root/'media');url='https://example.org/watch?vid=314&p=2&access_key=PRIVATE_KEY&sig=PRIVATE_SIG&jwt=PRIVATE_JWT&api_key=PRIVATE_API&url=https%3A%2F%2Fcdn.test%2Fx.m3u8%3Ftoken%3DPRIVATE_CDN';wid,eids=c.ingest({'site':'demo','key':url,'url':url,'entries':[{'key':url,'title':'one','url':url}]},'Title','',lib);c.patch_work(wid,check_hours=5)
   records=list((root/'media/.wvd-library').rglob('*.json'))
   for record in records:
    text=record.read_text(encoding='utf-8');self.assertNotIn('PRIVATE_',text,str(record))
    if json.loads(text)['payload']['works']:self.assertIn('vid=314',text);self.assertIn('p=2',text)
   c.close();c.path.unlink();fresh=Catalogue();fresh.mount_library(root/'media');work=fresh.work(wid);self.assertEqual(work['sources'][0]['url'],'https://example.org/watch?vid=314&p=2');self.assertEqual(fresh.episode(eids[0])['sources'][0]['key'],work['sources'][0]['work_key']);self.assertEqual(work['sources'][0]['mapping'],{'https://example.org/watch?vid=314&p=2':eids[0]});fresh.close()
