import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import requests
from scrapers.efficiency_pilot import PilotScraper, safe_article
from scrapers.source_search_runner import SourceSearchScraper, CnkiVerificationRequired
from scrapers.cnki_citation_network import build_article_id


def article(n):
    return {'file_name':str(n), 'db_name':'DB', 'title':str(n), 'source_journal_input':'J', 'journal':'J', 'publicationTime':'2025-01-01', 'detail_url':'https://kns.cnki.net/detail?v=SECRET', 'citation_network_status':'pending'}


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.s=self.make()

    def make(self):
        return PilotScraper(output_dir=self.root/'out', cache_dir=self.root/'cache', cookie='LID=test', env_path=self.root/'missing', delay_min=0, delay_max=0)

    def test_batch_reconciliation_scans_relation_cache_once(self):
        import json
        rows=[{**article(i),'citation_network_status':'success'} for i in range(3)]
        statuses={build_article_id(a):'success' for a in rows}
        for a in rows:
            key=json.dumps([1,1,build_article_id(a),'citations','CJFDREF',1])
            self.s.store.put('relations',key,{'saved_at':0})
        other_key=json.dumps([1,1,'OTHER:DB','citations','CJFDREF',1])
        self.s.store.put('relations',other_key,{'saved_at':0})
        def process(obj,journal,**kwargs):
            obj.enrich_articles(rows)
            return rows
        with patch.object(SourceSearchScraper,'process_journal',process), patch.object(self.s,'load_enriched_statuses',return_value=statuses), patch.object(SourceSearchScraper,'enrich_articles',return_value=rows), patch.object(self.s.store,'items',wraps=self.s.store.items) as items:
            self.s.process_journal('J',completed_articles={},enriched_by_id={},resume=True)
        self.assertEqual(sum(c.args==('relations',) for c in items.call_args_list),1)
        self.assertEqual(self.s.store.items('relations'),[(other_key,{'saved_at':0})])

    def test_unpersisted_success_does_not_clear_retry_or_relations(self):
        import json
        a={**article(1),'citation_network_status':'success'}
        aid=build_article_id(a)
        self.s.store.put('retries',aid,{'article':safe_article(a),'next_at':10**12})
        key=json.dumps([1,1,aid,'citations','CJFDREF',1])
        self.s.store.put('relations',key,{'saved_at':0})
        def process(obj,journal,**kwargs):
            obj.enrich_articles([a])
            return [a]
        with patch.object(SourceSearchScraper,'process_journal',process), patch.object(self.s,'load_enriched_statuses',return_value={}), patch.object(SourceSearchScraper,'enrich_articles',return_value=[a]), patch.object(self.s.store,'items',wraps=self.s.store.items) as items:
            self.s.process_journal('J',completed_articles={},enriched_by_id={},resume=True)
        self.assertEqual(sum(c.args==('relations',) for c in items.call_args_list),0)
        self.assertIsNotNone(self.s.store.get('retries',aid))
        self.assertIsNotNone(self.s.store.get('relations',key))

    def test_safe_article_never_persists_auth_context(self):
        a=article(1); a.update(detail_context={'kcms_link':'SECRET'}, reader_detail={'vid':'SECRET'}, cookie='SECRET')
        saved=safe_article(a)
        self.assertNotIn('SECRET',str(saved)); self.assertEqual(saved['file_name'],'1')

    def test_cached_plan_survives_restart_and_uses_one_root_probe(self):
        rows=[article(1),article(2)]
        with patch.object(self.s,'search_page',side_effect=[(rows[:1],2),(rows[:1],1),(rows[1:],1)]) as net:
            plan=list(self.s._iter_adaptive_date_slices('J','2025',1))
            self.assertEqual(net.call_count,3)
        other=self.make()
        with patch.object(other,'search_page',return_value=(rows[:1],2)) as net:
            cached=list(other._iter_adaptive_date_slices('J','2025',1))
            self.assertEqual(net.call_count,1)
        self.assertEqual([x[0] for x in cached],[x[0] for x in plan])
        self.assertNotIn('SECRET',str(list(other.store.items('plans'))))

    def test_expired_plan_rebuilds(self):
        with patch.object(self.s,'search_page',return_value=([article(1)],1)):
            list(self.s._iter_adaptive_date_slices('J','2025',1))
        for key,value in self.s.store.items('plans'):
            value['saved_at']=0; self.s.store.put('plans',key,value)
        with patch.object(self.s,'search_page',return_value=([article(2)],1)):
            plan=list(self.s._iter_adaptive_date_slices('J','2025',1))
        self.assertEqual(plan[0][1][0]['file_name'],'2')

    def test_cached_leaf_refreshes_url_and_detects_drift(self):
        a=article(1); a['_pilot_slice']='2025'; a['_pilot_members']=[build_article_id(a)]
        with patch.object(self.s,'search_page',return_value=([article(2)],1)):
            self.assertFalse(self.s.refresh_article_search_token(a))
        self.assertTrue(self.s.plan_drift)

    def test_retry_backoff_reset_and_never_drop(self):
        a=article(1)
        for expected in (1800,7200,21600,86400,86400):
            with patch('scrapers.efficiency_pilot.time.time',return_value=100):
                self.s.record_result({**a,'citation_network_status':'partial'})
            value=self.s.store.get('retries',build_article_id(a))
            self.assertEqual(value['next_at'],100+expected)
        self.s.record_result({**a,'citation_network_status':'success'})
        self.assertIsNone(self.s.store.get('retries',build_article_id(a)))

    def test_verification_bubbles_and_does_not_become_retry_result(self):
        with patch.object(SourceSearchScraper,'enrich_articles',side_effect=CnkiVerificationRequired('verify')):
            with self.assertRaises(CnkiVerificationRequired): self.s.enrich_articles([article(1)])
        self.assertEqual(self.s.store.items('retries'),[])

    def test_future_retries_are_skipped_but_not_success(self):
        a=article(1); self.s.record_result({**a,'citation_network_status':'partial'})
        completed={}; seen=[]
        def process(obj,journal,**kwargs):
            seen.append(build_article_id(a) in kwargs['completed_articles']); return [a]
        with patch.object(SourceSearchScraper,'process_journal',process):
            self.s.process_journal('J',completed_articles=completed,enriched_by_id={},resume=True)
        self.assertEqual(seen,[True]); self.assertNotIn(build_article_id(a),completed)

    def test_conditional_304_uses_only_matching_page_cache(self):
        a=article(1); response=requests.Response(); response.status_code=200
        response._content=b'{"code":0,"data":{"total":0,"data":[]}}'; response.headers['ETag']='"version1"'
        with patch.object(SourceSearchScraper,'request_with_retry',return_value=response):
            first=self.s.fetch_citation_list(a,'citations','CJFDREF')
        not_modified=requests.Response(); not_modified.status_code=304; not_modified._content=b''
        with patch.object(SourceSearchScraper,'request_with_retry',return_value=not_modified) as net:
            again=self.s.fetch_citation_list(a,'citations','CJFDREF')
        self.assertEqual(first,again)
        self.assertEqual(net.call_args.kwargs['headers']['If-None-Match'],'"version1"')

    def test_new_articles_precede_due_retries_and_completion_stays_strict(self):
        a,b=article(1),article(2)
        self.s.store.put('retries',build_article_id(a),{'article':safe_article(a),'attempts':1,'next_at':0,'status':'partial'})
        order=[]
        def enrich(obj,rows,*args,**kwargs):
            order.extend(r['file_name'] for r in rows)
            return [{**r,'article_id':build_article_id(r),'citation_network_status':('partial' if r['file_name']=='1' else 'success')} for r in rows]
        with patch.object(self.s,'search_page',return_value=([a,b],2)), patch.object(SourceSearchScraper,'enrich_articles',enrich):
            self.s.run(['J'],resume=True,auto_detect_years=True,year_start_floor=2025,year_end=2025,page_size=50,collect_results=False)
        self.assertEqual(order,['2','1'])
        import json
        checkpoint=json.loads((self.root/'out'/'checkpoint.json').read_text(encoding='utf-8'))
        self.assertNotIn('J',checkpoint['completed_journals'])
        self.assertTrue(self.s.store.get('retries',build_article_id(a)))
        # Next process must not retry A before its deadline; successful B is skipped.
        other=self.make()
        with patch.object(other,'search_page',return_value=([a,b],2)), patch.object(SourceSearchScraper,'enrich_articles') as net:
            other.run(['J'],resume=True,auto_detect_years=True,year_start_floor=2025,year_end=2025,page_size=50,collect_results=False)
        net.assert_not_called()

    def test_conditional_revalidation_uses_actual_base_304_handling(self):
        from scrapers.base_scraper import BaseScraper
        a=article(1)
        response=requests.Response(); response.status_code=200; response._content=b'{"code":0,"data":{"total":0,"data":[]}}'; response.headers['ETag']='"v1"'
        with patch.object(BaseScraper,'request_with_retry',return_value=response):
            expected=self.s.fetch_citation_list(a,'citations','CJFDREF')
        response=requests.Response(); response.status_code=304; response._content=b''
        with patch.object(BaseScraper,'request_with_retry',return_value=response):
            self.assertEqual(self.s.fetch_citation_list(a,'citations','CJFDREF'),expected)
        # Ordinary 304 without this caller's conditional page context must stay rejected.
        with patch.object(BaseScraper,'request_with_retry',return_value=response):
            with self.assertRaises(ValueError): self.s.request_with_retry('GET','https://kns.cnki.net/other')

    def test_no_validator_rechecks_saved_pages_and_does_not_cache_secrets(self):
        a=article(1)
        response=requests.Response(); response.status_code=200; response._content=b'{"code":0,"data":{"total":0,"data":[]},"token":"SECRET"}'
        with patch.object(SourceSearchScraper,'request_with_retry',return_value=response) as net:
            self.s.fetch_citation_list(a,'citations','CJFDREF')
            self.s.fetch_citation_list(a,'citations','CJFDREF')
        self.assertEqual(net.call_count,2)
        self.assertNotIn('If-None-Match',net.call_args.kwargs['headers'])
        self.assertNotIn('SECRET',str(self.s.store.items('relations')))

    def test_queue_bootstrap_requests_do_not_use_page_validator(self):
        self.s._page_context='some-page'
        response=requests.Response(); response.status_code=200; response._content=b'html'
        with patch.object(SourceSearchScraper,'request_with_retry',return_value=response):
            self.s.request_with_retry('GET','https://kns.cnki.net/kns8s/defaultresult/index')
        self.assertEqual(self.s.store.items('relations'),[])

    def test_cached_plan_cannot_finish_without_fresh_discovery(self):
        a=article(1)
        with patch.object(self.s,'search_page',return_value=([a],1)):
            list(self.s._iter_adaptive_date_slices('J','2025-01-01..2025-12-31',50))
        # Existing success, but journal isn't marked done yet.
        self.s.append_jsonl(self.root/'out'/'enriched.jsonl',{**a,'article_id':build_article_id(a),'citation_network_status':'success'})
        other=self.make()
        with patch.object(other,'search_page',return_value=([a],1)) as net:
            other.run(['J'],resume=True,auto_detect_years=True,year_start_floor=2025,year_end=2025,page_size=50,collect_results=False)
        self.assertEqual(net.call_count,2)
        import json
        d=json.loads((self.root/'out'/'checkpoint.json').read_text(encoding='utf-8'))
        self.assertIn('J',d['completed_journals'])

    def test_plan_benchmark_reduces_calls_without_changing_ids(self):
        from datetime import date, timedelta
        rows=[]
        for n in range(16):
            a=article(n); a['publicationTime']=(date(2025,1,1)+timedelta(days=n*20)).isoformat(); rows.append(a)
        def search(journal,page=1,page_size=2,year='2025'):
            start,end=self.s._slice_date_bounds(year)
            found=[a for a in rows if start<=date.fromisoformat(a['publicationTime'])<=end]
            return found[:page_size],len(found)
        with patch.object(self.s,'search_page',side_effect=search) as net:
            before=list(self.s._iter_adaptive_date_slices('J','2025',2)); full_calls=net.call_count
        other=self.make()
        with patch.object(other,'search_page',side_effect=search) as net:
            after=list(other._iter_adaptive_date_slices('J','2025',2)); cache_calls=net.call_count
        self.assertEqual({build_article_id(a) for _,rs,_ in before for a in rs},{build_article_id(a) for _,rs,_ in after for a in rs})
        self.assertLessEqual(cache_calls,full_calls/2)
        print('PLAN_BENCHMARK',full_calls,cache_calls,16)

    def test_large_single_day_leaf_does_not_page(self):
        rows = [article(n) for n in range(1, 52)]
        rows[-1]['detail_url'] = 'https://kns.cnki.net/fresh-last-page'
        target = {**rows[-1], 'detail_url': 'https://kns.cnki.net/old'}
        target.update({
            '_pilot_slice': '2025-01-20..2025-01-20',
            '_pilot_members': [build_article_id(a) for a in rows],
        })

        with patch.object(
            self.s,
            'search_page',
            side_effect=[(rows[:50], 51)],
        ) as search_page:
            self.assertFalse(self.s.refresh_article_search_token(target))

        self.assertEqual([call.kwargs['page'] for call in search_page.call_args_list], [1])
        self.assertEqual(target['detail_url'], 'https://kns.cnki.net/old')

    def test_missing_metrics_never_means_complete_success(self):
        with patch.object(self.s,'ensure_detail_context',return_value={}), patch.object(self.s,'fetch_citation_metrics',side_effect=ValueError('metrics failed')), patch.object(self.s,'fetch_citation_relation_items',return_value=[article(2)]):
            result=self.s.enrich_articles([article(1)])[0]
        self.assertNotEqual(result['citation_network_status'],'success')

    def test_401_retry_verification_and_304_are_rechecked(self):
        from scrapers.base_scraper import BaseScraper
        for status in (302,304):
            first=requests.Response(); first.status_code=401
            second=requests.Response(); second.status_code=status; second.headers['Location']='https://kns.cnki.net/verify/home'
            with patch.object(BaseScraper,'request_with_retry',side_effect=[first,second]), patch.object(self.s,'reload_shard_cookie',return_value=True):
                with self.assertRaises((CnkiVerificationRequired,ValueError)):
                    self.s.request_with_retry('GET','https://kns.cnki.net/restapi/citation-api/v1/literature/citations')

    def test_due_retry_drift_does_not_starve_next_item(self):
        from scrapers.efficiency_pilot import PlanChanged
        a,b=article(1),article(2)
        for row in (a,b):
            self.s.store.put('retries',build_article_id(row),{'article':safe_article(row),'attempts':1,'next_at':0,'status':'partial'})
        calls=[]
        def enrich(rows,*args,**kwargs):
            calls.append(rows[0]['file_name'])
            if rows[0]['file_name']=='1': raise PlanChanged('drift')
            return [{**rows[0],'citation_network_status':'success','article_id':build_article_id(rows[0])}]
        with patch.object(SourceSearchScraper,'process_journal',return_value=[a,b]),patch.object(self.s,'enrich_articles',side_effect=enrich):
            self.s.process_journal('J',{}, {},resume=True)
        self.assertEqual(calls,['1','2'])
        self.assertGreater(self.s.store.get('retries',build_article_id(a))['next_at'],0)
        self.assertIsNone(self.s.store.get('retries',build_article_id(b)))

if __name__=='__main__': unittest.main()
