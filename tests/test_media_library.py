import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from media_library import Catalogue,choose_format,component
from collection_media import age_page,bilibili_data,discover
from transfer_stats import LivePresentation


class Library(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.env=patch.dict(os.environ,WVD_DATA_DIR=str(self.root/'data'));self.env.start()
        self.store=Catalogue();self.lib=self.store.library(str(self.root/'videos'),'影视库')
        self.group={'site':'AGE动漫','key':'123','url':'https://www.agedm.io/play/123/1/1','title':'作品','entries':[{'key':str(i),'title':f'第{i}集','url':f'https://www.agedm.io/play/123/1/{i}'} for i in (1,2)]}
        self.wid,self.ids=self.store.ingest(self.group,'作品','第一季',self.lib)
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def test_seasons_distinct_repeat_idempotent_and_new_ep_detection(self):
        wid,ids=self.store.ingest(self.group,'作品','第一季',self.lib);self.assertEqual((wid,ids),(self.wid,self.ids))
        group=copy.deepcopy(self.group);group['key']='456';wid2,_=self.store.ingest(group,'作品','第二季',self.lib);self.assertNotEqual(wid,wid2)
        self.group['entries'].append({'key':'3','title':'第3集','url':'https://www.agedm.io/play/123/1/3'})
        _,ids=self.store.ingest(self.group,'作品','第一季',self.lib);self.assertEqual(len(ids),3);self.assertEqual(self.store.work(self.wid)['new_count'],1)
    def test_existing_files_survive_remove_external_delete_rejected(self):
        file=self.root/'old.mp4';file.write_bytes(b'original');self.store.attach(self.ids[0],file,True)
        with self.assertRaises(ValueError):self.store.delete_owned(self.ids[0],[str(file)])
        self.store.remove(self.wid);self.assertEqual(file.read_bytes(),b'original')
    def test_owned_deletion_requires_containment_and_leaves_record(self):
        file=self.store.output(self.wid)/'one.mp4';file.write_bytes(b'video');self.store.attach(self.ids[0],file)
        self.store.delete_owned(self.ids[0],[str(file)]);self.assertFalse(file.exists());self.assertEqual(self.store.episode(self.ids[0])['files'],[])
        secret=self.root/'secret.mp4';secret.write_bytes(b'keep');self.store.attach(self.ids[1],secret)
        with self.assertRaises(ValueError):self.store.delete_owned(self.ids[1],[str(secret)])
        self.assertTrue(secret.exists())
    def test_missing_and_offline_differ_no_implicit_relocation(self):
        file=self.store.output(self.wid)/'one.mp4';file.write_bytes(b'video');self.store.attach(self.ids[0],file);self.assertEqual(self.store.file_state(self.store.episode(self.ids[0])),'已下载')
        file.unlink();self.assertEqual(self.store.file_state(self.store.episode(self.ids[0])),'文件缺失')
        (self.root/'videos').rename(self.root/'detached');self.assertEqual(self.store.file_state(self.store.episode(self.ids[0])),'存储库离线')
        with self.assertRaises(ValueError):self.store.output(self.wid)
        self.assertFalse((self.root/'videos').exists())
    def test_cross_site_merge_requires_mapping_and_preserves_sources(self):
        group=copy.deepcopy(self.group);group['site']='B站'
        separate,_=self.store.ingest(group,'作品','第一季',self.lib);self.assertNotEqual(separate,self.wid)
        self.store.remove(separate)
        with self.assertRaises(ValueError):self.store.ingest(group,'作品','第一季',self.lib,self.wid,{})
        merged,ids=self.store.ingest(group,'作品','第一季',self.lib,self.wid,dict(zip(('1','2'),self.ids)))
        self.assertEqual(merged,self.wid);self.assertEqual(ids,self.ids);self.assertEqual(len(self.store.episode(ids[0])['sources']),2)
    def test_local_import_no_move_dedup_and_season_prompt_is_local(self):
        file=self.root/'old.mp4';file.write_bytes(b'original')
        wid=self.store.import_file(file,'旧影片');self.assertEqual(self.store.import_file(file,'旧影片'),wid)
        self.store.patch_work(self.wid,no_play_prompt=True);self.assertFalse(self.store.work(wid)['no_play_prompt'])
        self.assertTrue(file.exists());ep=next(e for e in self.store.read()['episodes'] if e['work_id']==wid);self.assertTrue(ep['files'][0]['external'])
    def test_migration_verified_before_commit_preserves_external(self):
        source=self.store.output(self.wid)/'one.mp4';source.write_bytes(b'media');external=self.root/'external.mp4';external.write_bytes(b'keep')
        self.store.attach(self.ids[0],source);self.store.attach(self.ids[1],external,True);dest=self.store.library(str(self.root/'other'))
        self.assertEqual(self.store.migrate(self.wid,dest),[]);self.assertFalse(source.exists());self.assertEqual(external.read_bytes(),b'keep')
        ep=self.store.episode(self.ids[0]);self.assertEqual(Path(ep['files'][0]['path']).read_bytes(),b'media')
    def test_migration_cancel_or_collision_never_deletes_source(self):
        source=self.store.output(self.wid)/'one.mp4';source.write_bytes(b'media');self.store.attach(self.ids[0],source);dest=self.store.library(str(self.root/'other'))
        with self.assertRaises(ValueError):self.store.migrate(self.wid,dest,lambda:True)
        self.assertEqual(source.read_bytes(),b'media');self.assertEqual(self.store.work(self.wid)['library_id'],self.lib)
        collision=self.root/'other/作品/第一季/one.mp4';collision.parent.mkdir(parents=True,exist_ok=True);collision.write_bytes(b'private')
        with self.assertRaises(ValueError):self.store.migrate(self.wid,dest)
        self.assertEqual(collision.read_bytes(),b'private');self.assertTrue(source.exists())
    def test_corrupt_catalogue_is_never_silently_reset(self):
        self.store.path.write_text('broken')
        with self.assertRaises(ValueError):self.store.library(str(self.root/'new'))
        self.assertEqual(self.store.path.read_text(),'broken')
    def test_quality_is_per_episode_with_strict_and_no_upward_fallback(self):
        info={'formats':[{'id':'720','height':720,'width':1280},{'id':'4k','height':2160,'width':3840}]}
        self.assertEqual(choose_format(info)['id'],'720')
        with self.assertRaises(ValueError):choose_format(info,1080,True)
        self.assertTrue(component('../CON') and '/' not in component('../CON'))
    def test_portrait_quality_uses_short_edge_and_unknown_source_is_explicit(self):
        info={'formats':[{'id':'portrait','width':1080,'height':1920}]}
        self.assertEqual(choose_format(info,1080,True)['id'],'portrait')
        unknown={'formats':[{'id':'source','width':0,'height':0}]}
        self.assertEqual(choose_format(unknown)['id'],'source')
        with self.assertRaises(ValueError):choose_format(unknown,strict=True)
    def test_age_lines_not_seasons_and_episode_keys_stable(self):
        text='<meta property="og:title" content="作品 - AGE动漫"><a href="/play/123/1/1">第01集</a><a href="/play/123/1/2">第02集</a><a href="/play/123/2/1">线路2</a>'
        group=age_page(text,self.group['url']);self.assertEqual(len(group['entries']),2);self.assertEqual(group['season'],'');self.assertEqual(group['key'],'123')
    def test_different_playback_lines_require_confirmed_episode_mapping(self):
        group=copy.deepcopy(self.group);group['variant']='1';a,ids=self.store.ingest(group,'作品','第一季',self.lib)
        group['variant']='2';group['entries'][1]['title']='特别篇';b,_=self.store.ingest(group,'作品','第一季',self.lib)
        self.assertNotEqual(a,b);self.store.remove(b)
        wid,_=self.store.ingest(group,'作品','第一季',self.lib,a,{'1':ids[0],'2':'__new__'})
        self.assertEqual(wid,a);self.assertEqual(len(self.store.work(a)['sources']),2)
        self.assertEqual(len([e for e in self.store.read()['episodes'] if e['work_id']==a]),3)
    def test_bilibili_parts_and_bangumi_identity(self):
        group=bilibili_data({'data':{'bvid':'BVfixture','title':'作品','pages':[{'cid':1,'page':1,'part':'上'},{'cid':2,'page':2,'part':'下'}]}},'url')
        self.assertEqual(group['entries'][1]['key'],'2');self.assertTrue(group['entries'][1]['url'].endswith('?p=2'))
        group=bilibili_data({'result':{'season_id':8,'title':'作品第二季','episodes':[{'id':9,'title':'1','long_title':'开篇'}]}},'url');self.assertEqual(group['season'],'第二季');self.assertEqual(group['key'],'season:8')
    def test_detail_entrypoints_route_and_media_resolution(self):
        text='<meta property="og:title" content="作品 - AGE动漫"><a href="/play/123/1/1">第01集</a><a href="/play/123/1/2">第02集</a><a href="/play/123/2/1">第01集</a>'
        group=age_page(text,'https://www.agedm.io/detail/123');self.assertEqual(len(group['entries']),2);self.assertEqual(len(group['routes']),2);self.assertIn('/play/123/1/',group['url'])
        responses=[(json.dumps({'result':{'media':{'season_id':8,'horizontal_picture':'https://example.com/banner.jpg'}}}),'url'),
                   (json.dumps({'result':{'season_id':8,'title':'作品','episodes':[{'id':9,'title':'1'}]}}),'url')]
        with patch('engine._text',side_effect=responses) as fetch:
            group=discover('https://www.bilibili.com/bangumi/media/md123')
        self.assertEqual(group['key'],'season:8');self.assertIn('banner.jpg',group['banner']);self.assertIn('media_id=123',fetch.call_args_list[0].args[0])
    def test_completed_task_sync_does_not_restore_old_paths_after_migration(self):
        file=self.store.output(self.wid)/'one.mp4';file.write_bytes(b'video');self.store.bind(self.ids[0],'a'*32)
        row={'id':'a'*32,'state':'completed','result':{'path':str(file)}};self.store.sync([row]);dest=self.store.library(str(self.root/'other'));self.store.migrate(self.wid,dest)
        self.store.sync([row]);files=self.store.episode(self.ids[0])['files'];self.assertEqual(len(files),1);self.assertNotEqual(files[0]['path'],str(file));self.assertTrue(Path(files[0]['path']).is_file())
    def test_import_existing_episode_does_not_create_a_duplicate(self):
        file=self.root/'old.mp4';file.write_bytes(b'old external')
        before=len(self.store.read()['episodes']);wid=self.store.import_file(file,'作品','第一季','第1集',self.wid,self.ids[0])
        self.assertEqual(wid,self.wid);self.assertEqual(len(self.store.read()['episodes']),before);self.assertTrue(self.store.episode(self.ids[0])['files'][0]['external']);self.assertTrue(file.exists())
    def test_image_work_keeps_original_archive_and_slideshow_independently(self):
        group={'site':'抖音','key':'post1','url':'https://www.douyin.com/note/1','kind':'images','entries':[{'key':'1','title':'图文','url':'https://www.douyin.com/note/1'}]}
        wid,ids=self.store.ingest(group,'图文作品','',self.lib)
        archive=self.root/'images.zip';archive.write_bytes(b'archive');video=self.root/'slideshow.mp4';video.write_bytes(b'video')
        self.store.attach(ids[0],archive,kind='images');self.store.attach(ids[0],video,kind='video')
        self.assertEqual(self.store.work(wid)['kind'],'images');self.assertEqual({f['kind'] for f in self.store.episode(ids[0])['files']},{'images','video'})
        formats={'formats':[{'id':'images','height':0},{'id':'slideshow','height':1080}]}
        self.assertEqual(choose_format(formats,kind='images')['id'],'images');self.assertEqual(choose_format(formats,kind='slideshow')['id'],'slideshow')
        with self.assertRaises(ValueError):self.store.ingest(group,'图文作品','',self.lib,self.wid,{'1':self.ids[0]})
    def test_cover_paths_remain_relative_to_portable_data(self):
        file=self.root/'data/library/covers/image.png';file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'image')
        self.store.patch_work(self.wid,cover=str(file));self.assertEqual(self.store.work(self.wid)['cover'],'library/covers/image.png')
    def test_completion_deadline_is_not_renewed_and_new_task_cancels_it(self):
        base={'completed':True,'active':0,'queued':0,'percent':100,'count':2,'speed':0,'transfers':[]}
        view=LivePresentation();self.assertFalse(view.apply(base,('a',),10)['idle']);self.assertFalse(view.apply(base,('a',),12.9)['idle']);self.assertTrue(view.apply(base,('a',),13)['idle'])
        self.assertFalse(view.apply({**base,'completed':False,'active':1},('a','b'),14)['idle']);self.assertFalse(view.apply(base,('a','b'),14.1)['idle'])
