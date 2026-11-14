import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import preferences
from transfer_stats import summarize,row_speed


class TransferStats(unittest.TestCase):
    def fixture(self):
        q=types.SimpleNamespace(active={},waiting=[],snapshots={},batch_rows={},deleting=set())
        for key,size,percent in [('big',1000,10),('small',100,100)]:
            q.batch_rows[key]={'id':key,'title':key,'state':'running','total_bytes':size,'download_percent':percent}
            q.active[key]=(None,None,threading.Event())
            q.snapshots[key]={'phase':'download','speed':200,'_speed_at':100}
        return q
    def test_weighted_bytes_not_mean_of_percentages(self):
        q=self.fixture();stats=summarize(q,101)
        self.assertAlmostEqual(stats['percent'],200/1100*100)
        self.assertEqual(stats['speed'],400);self.assertEqual(len(stats['transfers']),2)
    def test_stale_processing_paused_and_cancelled_rates_are_excluded(self):
        q=self.fixture();self.assertEqual(summarize(q,105)['speed'],0)
        q.snapshots['big']['phase']=None;q.batch_rows['small']['state']='paused'
        self.assertEqual(summarize(q,101)['speed'],0);self.assertFalse(summarize(q,101)['transfers'])
        q.batch_rows['small']['state']='running';q.active['small'][2].set()
        self.assertIsNone(row_speed(q,q.batch_rows['small'],101))
    def test_unknown_totals_are_not_invented_and_history_is_not_counted(self):
        q=self.fixture();q.batch_rows['big']['total_bytes']=None
        self.assertIsNone(summarize(q,101)['percent'])
        q.batch_rows['big']['state']='completed';q.batch_rows['small']['state']='completed'
        self.assertTrue(summarize(q,101)['completed']);self.assertEqual(summarize(q,101)['percent'],100)
        q.batch_rows.clear();self.assertEqual(summarize(q,101)['count'],0)
    def test_settings_merge_validation_and_old_config_compatibility(self):
        with tempfile.TemporaryDirectory() as folder,patch('preferences.app_data_dir',return_value=Path(folder)):
            target=Path(folder)/'settings.json';target.write_text(json.dumps({'parallel_downloads':7,'future_option':'keep'}))
            self.assertEqual(preferences.load()['close_action'],'ask')
            preferences.update(float_enabled=True,float_position=[-900,200],float_opacity=70)
            preferences.save_limit(3)
            cfg=preferences.load();self.assertEqual(cfg['parallel_downloads'],3);self.assertTrue(cfg['float_enabled'])
            self.assertEqual(cfg['float_position'],[-900,200]);self.assertEqual(json.loads(target.read_text())['future_option'],'keep')
            for kwargs in ({'float_opacity':0},{'float_enabled':'yes'},{'close_action':'kill'},{'float_position':[True,0]}):
                with self.assertRaises(ValueError):preferences.update(**kwargs)
            target.write_text('{bad');self.assertEqual(preferences.load(),preferences.DEFAULTS)
