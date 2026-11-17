import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scrapers.efficiency_pilot import PilotScraper
class QueueScheduleTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);p=Path(self.tmp.name)
  self.s=PilotScraper(output_dir=p/'out',cache_dir=p/'cache',cookie='LID=test',env_path=p/'missing',delay_min=0,delay_max=0)
 def put(self,j,next_at,status='partial'):
  self.s.store.put('retries',j,{'article':{'source_journal_input':j},'next_at':next_at,'status':status})
 def test_initial_and_daily_discovery(self):
  self.assertEqual(self.s.scheduled_journals(['A','B'],now=100),['A','B'])
  self.assertEqual(self.s.scheduled_journals(['A','B'],now=101),[])
  self.assertEqual(self.s.scheduled_journals(['A','B'],now=86500),['A','B'])
 def test_only_due_authorized_journals_not_future_or_other_shard(self):
  self.s.scheduled_journals(['A','B'],now=100)
  self.put('A',200);self.put('B',300);self.put('OTHER',0)
  self.assertEqual(self.s.scheduled_journals(['A','B'],now=200),['A'])
  self.assertEqual(self.s.store.get('retries','B')['status'],'partial')
 def test_future_queue_never_forces_discovery(self):
  self.s.scheduled_journals(['A'],now=100);self.put('A',90000)
  self.assertEqual(self.s.scheduled_journals(['A'],now=1000),[])
 def test_due_journal_preserves_configured_order(self):
  self.s.scheduled_journals(['B','A'],now=100);self.put('A',0);self.put('B',0)
  self.assertEqual(self.s.scheduled_journals(['B','A'],now=101),['B','A'])

class EmptyQueueDiscoveryTests(QueueScheduleTests):
 def test_empty_queue_waits_for_daily_discovery(self):
  self.s.scheduled_journals(['A'],now=100)
  with patch('scrapers.efficiency_pilot.time.time',return_value=101):
   self.assertEqual(self.s.pending_retry_wait(),900)
 def test_discovery_deadline_not_delayed_by_future_retries(self):
  self.s.scheduled_journals(['A'],now=100);self.put('A',100000)
  with patch('scrapers.efficiency_pilot.time.time',return_value=86400):
   self.assertEqual(self.s.pending_retry_wait(),100)

class MainQueueTests(unittest.TestCase):
 def test_main_skips_not_due_cycle_and_keeps_exit_protocol(self):
  import sys
  from unittest.mock import Mock
  from main import main
  scraper=Mock();scraper.blocked_reason=''
  scraper.scheduled_journals.side_effect=[['A'],[],['A']]
  scraper.pending_retry_wait.side_effect=[30,30,None]
  with patch.object(sys,'argv',['main.py','--journal','A','--efficiency-pilot']), patch('scrapers.efficiency_pilot.PilotScraper',return_value=scraper), patch('time.sleep'):
   self.assertEqual(main(),0)
  self.assertEqual(scraper.run.call_count,2)
  self.assertEqual(scraper.scheduled_journals.call_count,3)
