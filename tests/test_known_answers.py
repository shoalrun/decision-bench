import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from decision_bench.core import baseline, load_json, normalize, request_for, validate_dataset

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'data/shoalrun-known-v1.json'


class KnownAnswerTests(unittest.TestCase):
    def setUp(self):
        self.data = load_json(FIXTURE)

    def test_source_execution_reproduces_all_answers(self):
        validate_dataset(self.data)
        result = subprocess.run(['node', 'scripts/known_answers.mjs', 'verify', str(FIXTURE)],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Verified 96 known answers', result.stdout)

    def test_requests_ignore_gold_oracle_reviews_and_history(self):
        for case in self.data['cases']:
            original = request_for(self.data, case)
            changed = copy.deepcopy(case)
            changed.update(expected='SECRET_GOLD', oracle={'PRIVATE_ORACLE': True},
                           rationale='PRIVATE_REASON', split='PRIVATE_SPLIT', group='PRIVATE_GROUP',
                           history=[{'content': 'PRIVATE_HISTORY'}])
            self.assertEqual(original, request_for(self.data, changed))
            self.assertEqual(set(original), {'state', 'questions'})
            self.assertEqual(set(original['state']), {'query', 'passages', 'input'})
            self.assertEqual(original['state']['input'], case['input'])
            text = json.dumps(original)
            for marker in ('SECRET_GOLD', 'PRIVATE_ORACLE', 'PRIVATE_REASON', 'PRIVATE_SPLIT', 'PRIVATE_GROUP', 'PRIVATE_HISTORY'):
                self.assertNotIn(marker, text)
            normalize(baseline(original, 'uniform'), original)

    def test_changed_inputs_or_options_cannot_keep_old_oracle(self):
        changed = copy.deepcopy(self.data)
        changed['cases'][0]['input']['args'][0]['embeddings']['provider'] = 'tampered'
        with self.assertRaisesRegex(ValueError, 'model-visible'):
            validate_dataset(changed)
        changed = copy.deepcopy(self.data)
        choice = next(c for c in changed['cases'] if c['task'] == 'config-choice')
        first = next(iter(choice['options']))
        choice['options'][first] = {'tampered': True}
        with self.assertRaisesRegex(ValueError, 'model-visible'):
            validate_dataset(changed)

    def test_forged_answer_is_rejected_by_replay(self):
        changed = copy.deepcopy(self.data)
        case = next(c for c in changed['cases'] if c['task'] == 'config-validity' and c['expected'] == 'yes')
        case['oracle']['outcomes'][0] = {'ok': False, 'error': {'name': 'Error', 'message': 'forged'}}
        case['expected'] = 'no'
        # Even a self-consistent forged log cannot survive executable replay.
        validate_dataset(changed)
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / 'forged.json'
            fixture.write_text(json.dumps(changed))
            result = subprocess.run(['node', 'scripts/known_answers.mjs', 'verify', str(fixture)],
                                    cwd=ROOT, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Oracle output mismatch', result.stderr)

    def test_exact_calls_do_not_cross_splits(self):
        splits = {}
        for case in self.data['cases']:
            for call in case['oracle']['calls']:
                key = json.dumps(call, sort_keys=True)
                self.assertEqual(splits.setdefault(key, case['split']), case['split'])


if __name__ == '__main__':
    unittest.main()
