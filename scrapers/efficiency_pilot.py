"""Opt-in efficiency pilot. No credential/header URLs enter persisted pilot state."""
from __future__ import annotations
import hashlib
import json
import time

import requests
from scrapers.pilot_store import PilotStore
from scrapers.source_search_runner import SourceSearchScraper, CnkiVerificationRequired
from scrapers.cnki_citation_network import build_article_id

PLAN_TTL = 24 * 3600
PAGE_TTL = 6 * 3600
RETRY_DELAYS = (1800, 7200, 21600, 86400)
ARTICLE_FIELDS = {'source','file_name','db_name','db_type','title','authors','journal',
                  'publicationTime','abstract','source_journal_input','search_mode',
                  'quote_frequency','download_count','citation_network_status'}
META_FIELDS = {'TI','AU','LY','YE','FN','TN','resource','DB','DBCODE','DOI'}


def safe_article(article):
    return {k:v for k,v in article.items() if k in ARTICLE_FIELDS and isinstance(v,(str,int,float,bool))}


def signature(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class PlanChanged(RuntimeError):
    pass


class PilotScraper(SourceSearchScraper):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.store = PilotStore(self.output_dir / '.efficiency-pilot')
        self._plan_depth = 0
        self._fresh_plan = False
        self.plan_drift = False
        self._leaf_live = {}
        self._page_context = None
        self._metrics = {}
        self._session_fingerprint = self._cookie_fingerprint()
        self._plan_used_cache = False
        self._import_retries()
        for key,value in self.store.items('relations'):
            if not 0 <= time.time()-value.get('saved_at',0) < PAGE_TTL:
                self.store.delete('relations',key)

    def event(self,name,**fields):
        # Only callers' counters/timings, never URLs or credential values.
        print('[pilot] '+json.dumps({'event':name,**fields},ensure_ascii=True),flush=True)

    def _import_retries(self):
        # Reconcile from authoritative output; never make a partial record successful.
        path=self.output_dir/'enriched.jsonl'
        if not path.exists(): return
        latest={}
        with path.open(encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    a=json.loads(line); latest[a.get('article_id') or build_article_id(a)]=safe_article(a)
        for aid,a in latest.items():
            if a.get('citation_network_status')=='success':
                self.store.delete('retries',aid)
            elif not self.store.get('retries',aid):
                self.store.put('retries',aid,{'article':safe_article(a),'attempts':0,'next_at':time.time(),'status':a.get('citation_network_status','partial')})

    def _iter_adaptive_date_slices(self,journal_name,slice_value,page_size,cached=None):
        if self._plan_depth:
            yield from super()._iter_adaptive_date_slices(journal_name,slice_value,page_size,cached)
            return
        size=max(1,min(page_size,50))
        key=json.dumps([1,self.shard_id,self.shard_count,journal_name,slice_value,size])
        entry=self.store.get('plans',key)
        usable=entry and 0 <= time.time()-entry.get('saved_at',0) < PLAN_TTL and not self._fresh_plan
        root=cached or self.search_page(journal_name,page=1,page_size=size,year=slice_value)
        root_sig=signature([root[1],[build_article_id(a) for a in root[0]]])
        if usable and entry.get('root_signature')==root_sig:
            rows=entry.get('leaves',[])
            ids=[build_article_id(a) for leaf in rows for a in leaf['articles']]
            if len(ids)==len(set(ids)) and len(ids)<=root[1] and all(len(leaf['articles'])==leaf['total'] for leaf in rows):
                self._plan_used_cache=True
                self.event('plan_hit',leaves=len(rows),articles=len(ids))
                for leaf in rows:
                    articles=[]
                    members=[build_article_id(a) for a in leaf['articles']]
                    for a in leaf['articles']:
                        articles.append({**a,'_pilot_slice':leaf['slice'],'_pilot_members':members})
                    yield leaf['slice'],articles,leaf['total']
                return
        self._plan_depth+=1
        try:
            plan=list(super()._iter_adaptive_date_slices(journal_name,slice_value,size,cached=root))
        finally:
            self._plan_depth-=1
        ids=[build_article_id(a) for _,articles,_ in plan for a in articles]
        # 截断（放弃翻页）后叶子文章数会少于根区间声明的总数，只禁止超出。
        if len(ids)!=len(set(ids)) or len(ids)>root[1]:
            raise RuntimeError('Pilot plan failed ID/count validation')
        if root[1]==0 and not self.last_search_confirmed_empty:
            raise RuntimeError('Pilot refuses unconfirmed empty plan')
        self.store.put('plans',key,{'journal':journal_name,'saved_at':time.time(),'root_signature':root_sig,
            'leaves':[{'slice':s,'total':total,'articles':[safe_article(a) for a in rows]} for s,rows,total in plan]})
        self.event('plan_saved',leaves=len(plan),articles=len(ids))
        # Add safe leaf identity even to fresh rows for later retry persistence.
        for s,rows,total in plan:
            yield s,[{**a,'_pilot_slice':s,'_pilot_members':[build_article_id(r) for r in rows]} for a in rows],total

    def refresh_article_search_token(self,article):
        scope=article.get('_pilot_slice')
        if not scope: return super().refresh_article_search_token(article)
        journal=article.get('source_journal_input') or article.get('journal','')
        key=(journal,scope)
        cached=self._leaf_live.get(key)
        if cached is None or time.time()-cached[0]>300:
            rows,total=self.search_page(journal,page=1,page_size=50,year=scope)
            self._leaf_live[key]=(time.time(),rows,total)
        _,rows,total=self._leaf_live[key]
        members=article.get('_pilot_members',[])
        if members and {build_article_id(a) for a in rows}!=set(members):
            self.plan_drift=True
            self.event('plan_drift')
            return False
        fresh=next((a for a in rows if build_article_id(a)==build_article_id(article)),None)
        if not fresh or not fresh.get('detail_url'): return False
        article['detail_url']=fresh['detail_url']
        article.pop('detail_context',None); article.pop('reader_detail',None)
        return True

    def enrich_articles(self,articles,relation_page_size=10,max_relation_pages=0):
        for a in articles:
            if a.get('_pilot_slice') and not a.get('detail_url'):
                if not self.refresh_article_search_token(a):
                    raise PlanChanged('Cached leaf changed or fresh article link missing')
        results=super().enrich_articles(articles,relation_page_size,max_relation_pages)
        for a in results:
            # Do not leak pilot cursor fields into existing output schemas.
            for key in list(a):
                if key.startswith('_pilot_'): a.pop(key)
        return results

    def append_jsonl(self,path,item):
        cleaned={key:value for key,value in item.items() if not key.startswith('_pilot_')}
        return super().append_jsonl(path,cleaned)

    def record_result(self,article,source=None,relation_keys=None):
        aid=article.get('article_id') or build_article_id(article)
        if article.get('citation_network_status')=='success':
            self.store.delete('retries',aid)
            keys = (key for key,_ in self.store.items('relations')) if relation_keys is None else relation_keys
            for key in keys:
                if json.loads(key)[2]==aid: self.store.delete('relations',key)
            return
        prior=self.store.get('retries',aid) or {}
        attempts=int(prior.get('attempts',0))+1
        row={'article':safe_article(article),'attempts':attempts,
             'next_at':time.time()+RETRY_DELAYS[min(attempts-1,len(RETRY_DELAYS)-1)],
             'status':article.get('citation_network_status','failed')}
        if source and source.get('_pilot_slice'): row['slice']=source['_pilot_slice']
        self.store.put('retries',aid,row)
        self.event('retry_deferred',attempts=attempts,delay_seconds=RETRY_DELAYS[min(attempts-1,3)])

    def process_journal(self,journal_name,completed_articles,enriched_by_id,**kwargs):
        # Legacy implementation handles durable export before we acknowledge queue state.
        pending={aid:v for aid,v in self.store.items('retries') if v.get('article',{}).get('source_journal_input')==journal_name}
        due=sorted((aid for aid,v in pending.items() if v.get('next_at',0)<=time.time()),key=lambda a:pending[a].get('next_at',0))[:5]
        masked=dict(completed_articles)
        # First pass only processes new articles. Due retries run after the journal's new work.
        for aid in pending: masked[aid]={'citation_network_status':'deferred'}
        original_enrich=self.enrich_articles
        outputs=[]
        def tracked(rows,*args,**kw):
            result=original_enrich(rows,*args,**kw)
            outputs.extend(zip(result,rows))
            return result
        self.enrich_articles=tracked
        self._plan_used_cache=False
        self.plan_drift=False
        try:
            try:
                articles=super().process_journal(journal_name,completed_articles=masked,enriched_by_id=enriched_by_id,**kwargs)
            except PlanChanged:
                for key,value in self.store.items('plans'):
                    if value.get('journal')==journal_name: self.store.delete('plans',key)
                self._leaf_live.clear(); self._fresh_plan=True
                retry_kwargs={**kwargs,'seen_article_ids':None}
                articles=super().process_journal(journal_name,completed_articles=masked,enriched_by_id=enriched_by_id,**retry_kwargs)
        finally:
            self.enrich_articles=original_enrich
            self._fresh_plan=False
            # Only reconcile entries already persisted, including progress before verification.
            statuses=self.load_enriched_statuses() if kwargs.get('resume',True) else {}
            success_ids = {r.get('article_id') or build_article_id(r) for r,_ in outputs
                           if r.get('citation_network_status') == 'success'
                           and statuses.get(r.get('article_id') or build_article_id(r)) == 'success'}
            relation_keys = {aid: [] for aid in success_ids}
            if success_ids:
                for key,_ in self.store.items('relations'):
                    aid = json.loads(key)[2]
                    if aid in relation_keys: relation_keys[aid].append(key)
            for result,source in outputs:
                aid=result.get('article_id') or build_article_id(result)
                if statuses.get(aid)==result.get('citation_network_status'):
                    self.record_result(result,source,relation_keys=relation_keys.get(aid, []))
            for aid,status in statuses.items():
                if status=='success': completed_articles[aid]={'citation_network_status':'success'}
        # Bounded delayed queue, using a fresh leaf request, never stale URL state.
        by_id={build_article_id(a):a for a in articles}
        for aid in due:
            if aid in completed_articles: self.store.delete('retries',aid); continue
            a=by_id.get(aid)
            if a is None:
                # Keep disappeared IDs pending and visible; don't invent a replacement article.
                self.record_result(pending[aid]['article']); continue
            try:
                result=original_enrich([a],kwargs.get('relation_page_size',10),kwargs.get('max_relation_pages',0))[0]
            except PlanChanged:
                for key,value in self.store.items('plans'):
                    if value.get('journal')==journal_name: self.store.delete('plans',key)
                self._leaf_live.clear()
                self.record_result({**a,'citation_network_status':'partial'},a)
                self.event('retry_plan_drift_deferred')
                continue
            if kwargs.get('resume',True):
                from exporters import export_article_incrementally
                self.append_jsonl(self.output_dir/'enriched.jsonl',result)
                export_article_incrementally(result,self.output_dir)
                self.record_result(result,a)
            if kwargs.get('collect_results',True): enriched_by_id[aid]=result
            if result.get('citation_network_status')=='success': completed_articles[aid]={'citation_network_status':'success'}
        # Never mark completion solely from a cached discovery snapshot, or with a pending orphan.
        remaining=[v for _,v in self.store.items('retries') if v.get('article',{}).get('source_journal_input')==journal_name]
        if remaining: self.last_search_complete=False
        elif self._plan_used_cache and articles and all(build_article_id(a) in completed_articles for a in articles):
            self._fresh_plan=True
            try:
                articles=self.process_journal(journal_name,completed_articles,enriched_by_id,**{**kwargs,'seen_article_ids':None})
            finally: self._fresh_plan=False
        return articles

    def scheduled_journals(self, journal_names, now=None):
        """Discover daily; between discoveries visit only journals with due retries."""
        now = time.time() if now is None else now
        if now >= getattr(self, '_next_discovery_at', 0):
            self._next_discovery_at = now + PLAN_TTL
            self.event('discovery_cycle', journals=len(journal_names))
            return list(journal_names)
        due = {v.get('article', {}).get('source_journal_input')
               for _, v in self.store.items('retries')
               if v.get('next_at', 0) <= now}
        selected = [j for j in journal_names if j in due]
        if selected:
            self.event('due_retry_journals', journals=len(selected))
        return selected

    def pending_retry_wait(self):
        pending=self.store.items('retries')
        deadlines = [v.get('next_at', 0) for _, v in pending]
        if hasattr(self, '_next_discovery_at'):
            deadlines.append(self._next_discovery_at)
        if not deadlines: return None
        return max(30, min(900, min(deadlines) - time.time()))

    def fetch_citation_metrics(self,article):
        metrics=super().fetch_citation_metrics(article)
        self._metrics[build_article_id(article)]=metrics
        return metrics

    def fetch_citation_list(self,article,relation_type='references',file_type='CJFDREF',page=1,page_size=10):
        aid=build_article_id(article)
        self._page_context=json.dumps([1,self.shard_id,aid,relation_type,file_type,page,page_size,self._session_fingerprint,signature(self._metrics.get(aid,{}))])
        try: return super().fetch_citation_list(article,relation_type,file_type,page,page_size)
        finally: self._page_context=None

    def request_with_retry(self,method,url,**kwargs):
        key=self._page_context
        if not key or self._classify_endpoint(url) != 'citation-list':
            return super().request_with_retry(method,url,**kwargs)
        prior=self.store.get('relations',key)
        if prior and not 0<=time.time()-prior.get('saved_at',0)<PAGE_TTL: prior=None
        headers=dict(kwargs.get('headers') or {})
        etag=prior.get('etag') if prior else None
        if etag: headers['If-None-Match']=etag
        response=super().request_with_retry(method,url,**{**kwargs,'headers':headers,'_allow_not_modified':bool(etag)})
        if response.status_code==304:
            if not prior: raise RuntimeError('304 without validated page checkpoint')
            restored=requests.Response(); restored.status_code=200
            restored._content=json.dumps(prior['payload'],ensure_ascii=False).encode('utf-8'); restored.encoding='utf-8'
            self.event('relation_page_not_modified')
            return restored
        if response.status_code==200:
            payload=response.json()
            data=payload.get('data') if isinstance(payload,dict) else None
            if isinstance(payload,dict) and payload.get('code') in (0,'0',None) and isinstance(data,dict) and isinstance(data.get('data'),list):
                # Only public bibliographic metadata. Never serialize entire API bodies.
                clean=[]
                for row in data['data']:
                    if not isinstance(row,dict): continue
                    clean.append({'metadata':[m for m in row.get('metadata',[]) if isinstance(m,dict) and m.get('name') in META_FIELDS and isinstance(m.get('value'),(str,int,float))]})
                saved={'code':0,'data':{'total':data.get('total',0),'data':clean}}
                # Exact payload equivalence is required for conditional restoration.
                cache_etag=response.headers.get('ETag') if payload==saved else None
                self.store.put('relations',key,{'saved_at':time.time(),'etag':cache_etag,'payload':saved})
                self.event('relation_page_saved',conditional=bool(cache_etag))
        return response
