import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from tools.evaluate_impact_thresholds import (
    POLICIES, collect_snapshot, decide, evaluate, metrics, validate_cases, write_review_csv,
)


CASES = json.loads((Path(__file__).parent / 'fixtures/impact_threshold_cases.json').read_text())['cases']
BY_ID = {case['id']: case for case in CASES}


class ThresholdDecisionTests(unittest.TestCase):
    def result(self, case_id, policy=POLICIES[1]):
        return decide(BY_ID[case_id]['annotation'], policy)['direction']

    def test_decimal_boundaries_and_threshold_equality(self):
        self.assertEqual(self.result('eps_below_five'), 'neutral')
        self.assertEqual(self.result('eps_at_five'), 'positive')
        self.assertEqual(self.result('eps_minus_five'), 'negative')
        self.assertEqual(self.result('revenue_at_one'), 'positive')
        # Binary float 3.1 - 3.0 must not move a point across the 0.1 boundary.
        self.assertEqual(self.result('cpi_at_tenth_hot'), 'negative')
        self.assertEqual(self.result('cpi_at_tenth_cool'), 'positive')

    def test_eps_zero_and_losses_do_not_invert_the_direction(self):
        for case_id, expected in [('eps_zero_tiny', 'neutral'), ('eps_zero_below_floor', 'neutral'),
                                  ('eps_zero_at_floor', 'positive'), ('eps_zero_negative', 'negative'),
                                  ('eps_near_zero', 'neutral'), ('eps_loss_improves', 'positive'),
                                  ('eps_loss_worsens', 'negative'), ('revenue_zero_baseline', 'unknown')]:
            with self.subTest(case_id=case_id):
                self.assertEqual(self.result(case_id), expected)

    def test_mixed_requires_material_opposing_evidence(self):
        self.assertEqual(self.result('mixed_strong_both'), 'mixed')
        self.assertEqual(self.result('positive_with_tiny_negative'), 'positive')
        self.assertEqual(self.result('negative_with_tiny_positive'), 'negative')
        self.assertEqual(self.result('neutral_small_both'), 'neutral')
        self.assertEqual(self.result('cpi_core_conflict'), 'mixed')
        self.assertEqual(self.result('cpi_tiny_core_conflict'), 'positive')

    def test_duplicate_weak_evidence_cannot_add_up_to_a_signal(self):
        annotation = copy.deepcopy(BY_ID['eps_tiny_up']['annotation'])
        annotation['evidence'] *= 100
        self.assertEqual(decide(annotation, POLICIES[1])['direction'], 'neutral')

    def test_missing_evidence_is_never_implicitly_neutral(self):
        for case in CASES:
            if not case['annotation']['eligible']:
                self.assertEqual(decide(case['annotation'], POLICIES[1])['direction'], 'unknown')
        annotation = {'eligible': True, 'neutral_supported': False, 'evidence': []}
        self.assertEqual(decide(annotation, POLICIES[1])['direction'], 'unknown')
        annotation['evidence'] = copy.deepcopy(BY_ID['eps_tiny_up']['annotation']['evidence'])
        self.assertEqual(decide(annotation, POLICIES[1])['direction'], 'unknown')

    def test_policy_sweep_changes_borderlines_not_clear_events(self):
        self.assertEqual([self.result('eps_two_percent', policy) for policy in POLICIES],
                         ['positive', 'neutral', 'neutral'])
        self.assertEqual([self.result('buyback', policy) for policy in POLICIES], ['positive'] * 3)


class EvaluationTests(unittest.TestCase):
    def test_no_predictions_is_undefined_precision_not_perfect_accuracy(self):
        result = metrics(['positive', 'negative', 'unknown'], ['unknown'] * 3)
        self.assertEqual(result['coverage'], 0)
        self.assertIsNone(result['display_precision'])
        self.assertIsNone(result['classes']['positive']['precision'])
        self.assertEqual(result['classes']['positive']['recall'], 0)
        self.assertIsNone(result['classes']['mixed']['recall'])

    def test_error_counts_separate_bad_direction_and_unsupported_badge(self):
        result = metrics(['positive', 'negative', 'unknown', 'mixed', 'neutral'],
                         ['negative', 'positive', 'neutral', 'mixed', 'neutral'])
        self.assertEqual(result['opposite_direction_errors'], 2)
        self.assertEqual(result['unsupported_badges'], 1)
        self.assertEqual(result['display_precision'], 2 / 5)

    def test_event_group_cannot_leak_across_splits(self):
        cases = copy.deepcopy(CASES[:2])
        cases[1]['group_id'] = cases[0]['group_id']
        cases[1]['split'] = 'test'
        with self.assertRaisesRegex(ValueError, 'different splits'):
            evaluate(cases, 'development')

    def test_test_split_requires_a_preselected_policy(self):
        cases = copy.deepcopy(CASES[:2])
        for case in cases:
            case['split'] = 'test'
        with self.assertRaisesRegex(ValueError, 'one fixed policy'):
            evaluate(cases, 'test')
        report = evaluate(cases, 'test', 'conservative')
        self.assertEqual(set(report['results']), {'current_rules', 'conservative'})

    def test_measurements_require_valid_units_and_grounded_quotes(self):
        for key, value in [('unit', 'EUR_per_share'), ('actual', 'NaN'), ('quote', 'made up text')]:
            cases = copy.deepcopy(CASES[:1])
            cases[0]['annotation']['evidence'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_cases(cases)

    def test_report_is_explicitly_a_draft_policy_experiment(self):
        report = evaluate(CASES, 'development')
        self.assertEqual(report['label_status_counts'], {'draft': len(CASES)})
        self.assertEqual(report['origin_counts'], {'synthetic': len(CASES)})
        self.assertTrue(report['limitations'])
        self.assertNotIn('best_policy', report)
        self.assertGreater(report['results']['current_rules']['classes']['neutral']['predicted'], 0)
        for name in ('sensitive', 'balanced', 'conservative'):
            self.assertEqual(report['results'][name]['total'], len(CASES))
        with self.assertRaisesRegex(ValueError, 'No cases'):
            evaluate(CASES, 'test')

    def test_local_snapshot_deduplication_and_blind_review_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = {'id': 'one', 'title': '=SUM(1,2)', 'entities': [], 'publishedAt': '2026-11-10T00:00:00Z'}
            first, second = root / 'feed1.json', root / 'feed2.json'
            original = json.dumps({'items': [item]})
            first.write_text(original)
            second.write_text(original)
            snapshot = collect_snapshot([first, second])
            self.assertEqual(len(snapshot), 1)
            self.assertIsNone(snapshot[0]['expected'])
            self.assertEqual(first.read_text(), original)
            path = root / 'review.csv'
            write_review_csv(path, snapshot)
            with path.open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]['expected'], '')
            self.assertNotIn('current_assessment', rows[0])
            self.assertEqual(rows[0]['title'], "'=SUM(1,2)")
            self.assertEqual(rows[0]['published_at'], item['publishedAt'])


if __name__ == '__main__':
    unittest.main()
