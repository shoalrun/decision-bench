import unittest
from scripts.presentation_results import paired


def row(key, expected, prediction, status='ok'):
    return dict(base_case_id=key, expected=expected, prediction=prediction, status=status)


class PresentationPairingTests(unittest.TestCase):
    def test_pairs_by_identity_not_order_and_counts_regressions(self):
        left = [row('a', 'yes', 'no'), row('b', 'yes', 'yes'), row('c', 'no', 'no')]
        right = [row('c', 'no', 'no'), row('a', 'yes', 'yes'), row('b', 'yes', 'no')]
        result = paired(left, right)
        self.assertEqual((result['improved'], result['regressed'], result['both_correct'], result['net_correct_change']), (1, 1, 1, 0))

    def test_error_cannot_count_as_correct(self):
        result = paired([row('a', 'yes', 'yes', 'error')], [row('a', 'yes', 'yes')])
        self.assertEqual((result['improved'], result['left_errors']), (1, 1))

    def test_missing_or_duplicate_cases_fail(self):
        a = row('a', 'yes', 'yes')
        for left, right in (([a], []), ([a, a], [a])):
            with self.assertRaises(ValueError):
                paired(left, right)

    def test_changed_gold_fails(self):
        with self.assertRaises(ValueError):
            paired([row('a', 'yes', 'yes')], [row('a', 'no', 'yes')])
