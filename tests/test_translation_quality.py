from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import unittest
import unicodedata
from unittest.mock import patch

import test_pipeline as fixture
from test_pipeline import relay, storage
from tools.translation_quality import inspect_translation


class TranslationQualityTests(unittest.TestCase):
    setUp = fixture.PipelineTests.setUp

    def test_mixed_script_cases_match_browser_policy(self):
        cases = json.loads(Path(__file__).with_name('translation_quality_cases.json').read_text())
        for case in cases:
            with self.subTest(text=case['text']):
                text, issue = inspect_translation(case['source'], case['text'])
                self.assertEqual(issue, case.get('issue', ''))
                self.assertEqual(text, case.get('expected', ''))
        self.assertEqual(inspect_translation('Acme', unicodedata.normalize('NFD', '애크姆'))[1], 'mixed_script')
        self.assertEqual(inspect_translation('Acme', 'Acme姆', 'JA'), ('Acme姆', ''))

    def test_known_cached_name_is_repaired_without_paid_translation(self):
        self.args.translate_provider = 'deepl'
        item = dict(self.item, title='TransDigm Group earnings')
        key = relay.translation_cache_key(item, self.args)
        storage.atomic_write_json(Path(self.args.translate_cache), {key:'트랜스디그姆 실적'})
        with patch.object(relay.DeepLTranslator, 'translate_item') as api:
            items, billed = relay.translate_items([item], self.args)
        api.assert_not_called()
        self.assertEqual(items[0]['titleKo'], 'TransDigm 실적')
        self.assertEqual(billed, 0)
        self.assertEqual(storage.read_json(Path(self.args.translate_cache))[key], 'TransDigm 실적')

    def test_new_rejection_counts_billing_and_is_not_retried_after_restart(self):
        self.args.translate_provider = 'deepl'
        item = dict(self.item, title='Acme earnings')
        key = relay.translation_cache_key(item, self.args)
        with patch.object(relay.DeepLTranslator, 'translate_item', return_value=relay.TranslationResult('애크姆 실적', 'deepl', False, 12)) as api:
            items, billed = relay.translate_items([item], self.args)
            self.assertEqual(billed, 12)
            self.assertEqual(items[0]['titleKo'], '')
            self.assertEqual(items[0]['translationStatus'], 'review')
            relay.translate_items([item], self.args)
            worker = relay.BackgroundTranslator(self.args)
            try:
                restored, billed = worker.translate([item], self.args)
                self.assertEqual(restored[0]['title'], 'Acme earnings')
                self.assertEqual(restored[0]['translationStatus'], 'review')
                self.assertIsNone(worker.future)
            finally:
                worker.close()
        self.assertEqual(api.call_count, 1)
        cached = storage.read_json(Path(self.args.translate_cache))
        self.assertEqual(cached[key]['status'], 'review')
        self.assertNotIn('姆', json.dumps(cached, ensure_ascii=False))

    def test_old_bad_cache_is_rejected_even_without_translation_credentials(self):
        self.args.translate_provider = 'deepl'
        item = dict(self.item, title='Acme earnings')
        key = relay.translation_cache_key(item, self.args)
        storage.atomic_write_json(Path(self.args.translate_cache), {key:'애크姆 실적', 'unrelated':'美 증시 상승'})
        with patch.object(relay.DeepLTranslator, 'translate_item') as api:
            worker = relay.BackgroundTranslator(self.args)
            try:
                items, _ = worker.translate([item], self.args)
                self.assertEqual(items[0]['translationStatus'], 'review')
                self.assertIsNone(worker.future)
            finally:
                worker.close()
        api.assert_not_called()
        self.assertEqual(storage.read_json(Path(self.args.translate_cache))['unrelated'], '美 증시 상승')

    def test_background_rejected_result_does_not_schedule_repeated_jobs(self):
        self.args.translate_provider = 'deepl'
        worker = relay.BackgroundTranslator(self.args)
        try:
            with patch.object(relay.DeepLTranslator, 'translate_item', return_value=relay.TranslationResult('애크姆 실적', 'deepl', False, 12)) as api:
                worker.translate([self.item], self.args)
                worker.future.result(timeout=2)
                items, billed = worker.translate([self.item], self.args)
                self.assertEqual(items[0]['translationStatus'], 'review')
                self.assertEqual(billed, 12)
                worker.translate([self.item], self.args)
                self.assertIsNone(worker.future)
                self.assertEqual(api.call_count, 1)
        finally:
            worker.close()

    def test_concurrent_cache_migration_and_translation_keep_both_updates(self):
        path = Path(self.args.translate_cache)
        storage.atomic_write_json(path, {'old':'연준 발표'})
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(relay.save_translation_cache, path, {key:value})
                       for key,value in [('review', relay.review_record()), ('new', '美 증시 상승')]]
            for future in futures:
                future.result(timeout=2)
        self.assertEqual(set(storage.read_json(path)), {'old', 'review', 'new'})

    def test_retained_feed_and_custom_replacements_cannot_bypass_guard(self):
        item = dict(self.item, title='Acme earnings', titleKo='애크姆 실적')
        storage.atomic_write_json(Path(self.args.out), {'items':[item], 'updatedAt':self.now.isoformat()})
        relay.write_payload(Path(self.args.out), [], {'source_statuses':{'massive':{'status':'error'}}})
        saved = storage.read_json(Path(self.args.out))
        self.assertEqual(saved['items'][0]['titleKo'], '')
        self.assertEqual(saved['items'][0]['translationStatus'], 'review')
        self.assertEqual(saved['meta']['translation_review_count'], 1)
        checked, entry = relay.checked_translation(self.item, '연준 발표', self.args, {'연준':'연준姆'})
        self.assertEqual(checked['translationStatus'], 'review')


if __name__ == '__main__':
    unittest.main()
