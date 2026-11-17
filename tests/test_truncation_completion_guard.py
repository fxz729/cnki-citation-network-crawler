import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scrapers.source_search_runner import SourceSearchScraper, build_article_id


REASONS = (
    'count-not-conserved', 'non-convergent', 'single-day-overflow',
    'fixed-slice-overflow', 'source-filtered', 'confirmed-source-duplicate',
    'future-unknown-reason',
)


class TruncationCompletionGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.scraper = SourceSearchScraper(
            output_dir=root / 'output', cache_dir=root / 'cache',
            cookie='test=value', env_path=root / 'missing.env',
            checkpoint_path=root / 'output' / 'checkpoint.json',
        )
        self.path = self.scraper.output_dir / 'truncated_slices.jsonl'

    def write_records(self, records):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')

    def record(self, reason, **extra):
        return dict(journal='Journal', slice='2020', declared_total=100,
                    taken=50, reason=reason, **extra)

    def test_missing_audit_has_no_blockers(self):
        self.assertEqual(self.scraper.audit_truncation_resolutions('Journal'), [])

    def test_all_unresolved_reasons_block_with_explicit_resolution(self):
        for reason in REASONS:
            with self.subTest(reason=reason):
                self.write_records([self.record(reason)])
                audit = self.scraper.audit_truncation_resolutions('Journal')
                self.assertEqual(len(audit), 1)
                self.assertEqual(audit[0]['reason'], reason)
                expected = 'source-filtered-unverified' if reason == 'source-filtered' else 'unresolved'
                self.assertEqual(audit[0]['resolution'], expected)

    def test_history_preserved_without_inventing_missing_article_count(self):
        record = self.record('count-not-conserved', resolution='success')
        self.write_records([record, record])
        before = self.path.read_bytes()
        audit = self.scraper.audit_truncation_resolutions('Journal')
        self.assertEqual(len(audit), 2)
        self.assertTrue(all(r['resolution'] == 'unresolved' for r in audit))
        self.assertTrue(all('missing_count' not in r for r in audit))
        self.assertEqual(self.path.read_bytes(), before)

    def test_other_journal_is_not_blocked(self):
        self.write_records([self.record('single-day-overflow')])
        self.assertEqual(self.scraper.audit_truncation_resolutions('Other'), [])

    def run_completion(self, records, empty=False):
        self.write_records(records)
        article = dict(file_name='A1', db_name='CJFQ', title='Article')

        def process(*args, **kwargs):
            self.scraper.last_search_complete = True
            self.scraper.last_search_confirmed_empty = empty
            if empty:
                return []
            kwargs['completed_articles'][build_article_id(article)] = {'citation_network_status': 'success'}
            return [article]

        with patch.object(self.scraper, 'process_journal', side_effect=process), patch(
            'scrapers.source_search_runner.save_checkpoint'
        ) as save, patch('scrapers.source_search_runner.export_all_formats_from_jsonl'):
            self.scraper.run(['Journal'], resume=True, collect_results=False)
        return save.call_args.args[1]['completed_journals']

    def test_run_blocks_despite_successful_articles_or_confirmed_empty(self):
        for reason in REASONS:
            for empty in (False, True):
                with self.subTest(reason=reason, empty=empty):
                    self.assertNotIn('Journal', self.run_completion([self.record(reason)], empty))

    def test_clean_journal_can_still_complete(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                self.assertIn('Journal', self.run_completion([], empty))


if __name__ == '__main__':
    unittest.main()
