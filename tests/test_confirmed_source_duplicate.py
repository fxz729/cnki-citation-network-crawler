import unittest,tempfile,json
from pathlib import Path
import scrapers.source_search_runner as runner

class ConfirmedDuplicateTests(unittest.TestCase):
 def article(self,**kw):
  return dict(file_name='DWDC201707027',db_name='CJFQ',title='哈萨克斯坦媒体生态及其特征',authors='胡邦胜',journal='对外传播',publicationTime='2017-07-22',**kw)
 def test_confirmed_duplicate_removed(self):
  a=self.article();b={**a,'detail_url':'different-expiring-token'}
  rows,n=runner.deduplicate_confirmed_source_rows([a,b],'对外传播')
  self.assertEqual(rows,[a]);self.assertEqual(n,1)
 def test_verified_macro_quality_duplicate_removed(self):
  a=dict(file_name='HGLY20261020006',db_name='CAPJ',title='中国“投资于人”型财政支出的国际比较与效能评估',authors='王增文;罗佩玲',journal='宏观质量研究',publicationTime='2026-10-20 17:25')
  rows,n=runner.deduplicate_confirmed_source_rows([a,{**a,'detail_url':'fresh-token'}],'宏观质量研究')
  self.assertEqual(rows,[a]);self.assertEqual(n,1)
  rows,n=runner.deduplicate_confirmed_source_rows([a,{**a,'title':'conflict'}],'宏观质量研究')
  self.assertEqual(len(rows),2);self.assertEqual(n,0)
 def test_unknown_duplicate_not_removed(self):
  a={**self.article(),'file_name':'OTHER'}
  rows,n=runner.deduplicate_confirmed_source_rows([a,a],'对外传播')
  self.assertEqual(len(rows),2);self.assertEqual(n,0)
 def test_conflicting_metadata_not_removed(self):
  a=self.article();b={**a,'title':'Different'}
  rows,n=runner.deduplicate_confirmed_source_rows([a,b],'对外传播')
  self.assertEqual(len(rows),2);self.assertEqual(n,0)
 def test_other_journal_not_removed(self):
  a=self.article();rows,n=runner.deduplicate_confirmed_source_rows([a,a],'Other')
  self.assertEqual(len(rows),2);self.assertEqual(n,0)
 def test_duplicate_record_blocks_completion_after_restart(self):
  with tempfile.TemporaryDirectory() as p:
   s=runner.SourceSearchScraper(output_dir=p,cache_dir=p,shard_id=7)
   self.assertFalse(s.has_confirmed_source_duplicate('对外传播'))
   Path(p,'truncated_slices.jsonl').write_text(json.dumps({'journal':'对外传播','reason':'confirmed-source-duplicate'})+'\n',encoding='utf-8')
   self.assertTrue(s.has_confirmed_source_duplicate('对外传播'))
   self.assertFalse(s.has_confirmed_source_duplicate('Other'))
 def test_completion_guard_present(self):
  import inspect
  self.assertIn('and not self.has_confirmed_source_duplicate(journal_name)',inspect.getsource(runner.SourceSearchScraper.run))
if __name__=='__main__':unittest.main()
