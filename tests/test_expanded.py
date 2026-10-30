import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from collections import Counter

from decision_bench.core import baseline, load_json, normalize, request_for, validate_dataset

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'data/shoalrun-expanded-v1.json'


class ExpandedTests(unittest.TestCase):
    def setUp(self):
        self.data = load_json(FIXTURE)

    def test_all_known_answers_replay(self):
        validate_dataset(self.data)
        result = subprocess.run(['node', 'scripts/expanded_cases.mjs', 'verify', str(FIXTURE)],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Verified 400 expanded known answers', result.stdout)
        self.assertEqual(Counter(c['split'] for c in self.data['cases']), {'dev': 200, 'test': 200})
        for function in {c['input']['function'] for c in self.data['cases']}:
            selected = [c for c in self.data['cases'] if c['input']['function'] == function]
            self.assertEqual(len(selected), 80)
            for split in ('dev', 'test'):
                cases = [c for c in selected if c['split'] == split]
                self.assertEqual(len(cases), 40)
                if cases[0]['task'] in ('function-validity', 'boolean-result'):
                    self.assertEqual(Counter(c['expected'] for c in cases), {'yes': 20, 'no': 20})

    def test_payload_excludes_gold_and_uses_one_reference(self):
        for case in self.data['cases']:
            original = request_for(self.data, case)
            changed = copy.deepcopy(case)
            changed.update(expected='PRIVATE_GOLD', oracle={'PRIVATE_ORACLE': True},
                           id='PRIVATE_ID', split='PRIVATE_SPLIT', group='PRIVATE_GROUP',
                           rationale='PRIVATE_REASON', history=['PRIVATE_HISTORY'])
            self.assertEqual(original, request_for(self.data, changed))
            self.assertEqual(set(original), {'state', 'questions'})
            self.assertEqual(set(original['state']), {'query', 'passages', 'input'})
            self.assertEqual(set(original['state']['input']), {'function', 'args'})
            self.assertEqual(len(original['state']['passages']), 1)
            self.assertEqual(original['state']['passages'][0]['text'],
                             self.data['references'][case['input']['module']]['text'])
            self.assertNotIn('PRIVATE_', json.dumps(original))
            normalize(baseline(original, 'uniform'), original)

    def test_reader_outcome_options_do_not_disclose_return_or_throw(self):
        for case in self.data['cases']:
            if case['input']['function'] == 'pageReaderFilters':
                self.assertEqual(sum(value['ok'] for value in case['options'].values()), 2)

    def test_tampered_input_and_ambiguous_choices_fail(self):
        changed = copy.deepcopy(self.data)
        changed['cases'][0]['input']['args'] = ['tampered']
        with self.assertRaisesRegex(ValueError, 'visible input'):
            validate_dataset(changed)
        changed = copy.deepcopy(self.data)
        c = next(c for c in changed['cases'] if c['task'] == 'function-outcome')
        keys = list(c['options'])
        c['options'][keys[1]] = c['options'][keys[0]]
        with self.assertRaisesRegex(ValueError, 'distinct outcomes'):
            validate_dataset(changed)

    def test_forged_outcome_fails_executable_replay(self):
        changed = copy.deepcopy(self.data)
        c = next(c for c in changed['cases'] if c['task'] == 'function-validity' and c['expected'] == 'yes')
        c['oracle']['outcome'] = {'ok': False, 'error': {'name': 'Error', 'message': 'forged'}}
        c['expected'] = 'no'
        validate_dataset(changed)
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'forged.json'
            p.write_text(json.dumps(changed))
            result = subprocess.run(['node', 'scripts/expanded_cases.mjs', 'verify', str(p)],
                                    cwd=ROOT, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Oracle outcome mismatch', result.stderr)

    def test_duplicate_calls_fail_even_if_split_changed(self):
        changed = copy.deepcopy(self.data)
        c = copy.deepcopy(changed['cases'][0])
        c.update(id='duplicate', group='different-family', split='test')
        changed['cases'].append(c)
        with self.assertRaisesRegex(ValueError, 'Duplicate expanded input'):
            validate_dataset(changed)

    def test_cli_runs_and_resumes_expanded_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / 'expanded'
            args = [sys.executable, '-m', 'decision_bench', 'run', '--dataset', str(FIXTURE),
                    '--models', 'uniform', '--limit', '10', '--out', str(out)]
            first = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            before = (out / 'results.jsonl').read_text()
            self.assertEqual(len(before.splitlines()), 10)
            self.assertEqual(len(load_json(out / 'summary.json')['models']['uniform']['tasks']), 3)
            resumed = subprocess.run(args + ['--resume'], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(before, (out / 'results.jsonl').read_text())


if __name__ == '__main__':
    unittest.main()
