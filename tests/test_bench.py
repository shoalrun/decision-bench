import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from io import BytesIO

from decision_bench.core import baseline, digest, load_json, normalize, request_for, validate_dataset
from decision_bench.metrics import score, summarize
from decision_bench.transport import call_remote, check_url

ROOT = Path(__file__).resolve().parents[1]


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.data = load_json(ROOT / 'tests/fixtures/schema-transport-v1.json')

    def test_frozen_data_and_no_group_leakage(self):
        validate_dataset(self.data)
        self.assertEqual(len(self.data['cases']), 96)
        changed = copy.deepcopy(self.data)
        changed['passages']['p01']['text'] += 'changed'
        with self.assertRaisesRegex(ValueError, 'hash'):
            validate_dataset(changed)
        changed = copy.deepcopy(self.data)
        changed['cases'][0]['split'] = 'test'
        with self.assertRaisesRegex(ValueError, 'crosses'):
            validate_dataset(changed)

    def test_gold_and_rationales_never_leave(self):
        case = self.data['cases'][0]
        request = request_for(self.data, case)
        changed = dict(case, expected='none', rationale='SECRET', split='test', id='PRIVATE')
        self.assertEqual(request, request_for(self.data, changed))
        text = json.dumps(request)
        for field in ('expected', 'rationale', 'source', 'sha256', 'split'):
            self.assertNotIn('"' + field + '"', text)

    def test_normalization_rejects_invalid_distributions(self):
        req = request_for(self.data, self.data['cases'][0])
        raw = baseline(req, 'uniform')
        normalize(raw, req)
        options = list(req['questions']['decision']['criteria'])
        for bad in (-.1, float('nan'), float('inf'), True):
            changed = copy.deepcopy(raw)
            changed['answers']['decision']['probabilities'][options[0]] = bad
            with self.assertRaises(ValueError):
                normalize(changed, req)
        changed = copy.deepcopy(raw)
        changed['answers']['decision']['probabilities'].pop(options[0])
        with self.assertRaises(ValueError):
            normalize(changed, req)
        changed = copy.deepcopy(raw)
        changed['answers']['decision']['probabilities'] = {k: .1 for k in options}
        with self.assertRaisesRegex(ValueError, 'sum'):
            normalize(changed, req)
        changed = copy.deepcopy(raw)
        changed['answers']['decision']['choice'] = 'unknown'
        with self.assertRaises(ValueError):
            normalize(changed, req)

    def test_selected_probability_not_vendor_confidence(self):
        req = request_for(self.data, self.data['cases'][0])
        raw = baseline(req, 'uniform')
        raw['answers']['decision']['confidence'] = .999
        self.assertEqual(normalize(raw, req)['confidence'], .2)
        req = request_for(self.data, self.data['cases'][1])
        raw = {'answers': {'decision': {'type': 'noul', 'noul': .1}}}
        normalized = normalize(raw, req)
        self.assertEqual(normalized['prediction'], 'no')
        self.assertEqual(normalized['confidence'], .9)

    def test_metrics_hand_calculated_and_errors_count(self):
        def row(label, probs, expected):
            return {'status': 'ok', 'prediction': label, 'probabilities': probs,
                    'confidence': probs[label], 'expected': expected, 'latency_ms': 10}
        rows = [row('yes', {'yes': .8, 'no': .2}, 'yes'),
                row('no', {'yes': .1, 'no': .9}, 'yes'), {'status': 'error'}]
        result = score(rows)
        self.assertAlmostEqual(result['accuracy'], 1 / 3)
        self.assertEqual(result['valid_accuracy'], .5)
        self.assertAlmostEqual(result['brier'], (.08 + 1.62) / 2)
        self.assertAlmostEqual(result['nll'], (-math.log(.8) - math.log(.1)) / 2)
        threshold = next(t for t in result['thresholds'] if t['threshold'] == .9)
        self.assertEqual(threshold['accepted'], 1)
        self.assertEqual(threshold['accuracy'], 0)
        self.assertAlmostEqual(threshold['coverage'], 1 / 3)
        failed = score([{'status': 'error'}])
        self.assertEqual(failed['accuracy'], 0)
        self.assertIsNone(failed['brier'])

    def test_paired_results_intersect_valid_cases(self):
        rows = []
        for model, predictions in [('a', ['yes', 'yes']), ('b', ['no', None])]:
            for i, pred in enumerate(predictions):
                r = {'model_name': model, 'case_id': str(i), 'task': 'answerability',
                     'expected': 'yes', 'status': 'ok' if pred else 'error'}
                if pred:
                    r.update(prediction=pred, probabilities={'yes': .8 if pred == 'yes' else .2,
                                                            'no': .2 if pred == 'yes' else .8},
                             confidence=.8, latency_ms=1)
                rows.append(r)
        pair = summarize(rows)['paired'][0]
        self.assertEqual((pair['common_valid'], pair['a_wins'], pair['b_wins']), (1, 1, 0))


class IntegrationTests(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'decision_bench', *map(str, args)],
                              cwd=ROOT, text=True, capture_output=True)

    def test_offline_report_resume_and_drift_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / 'run'
            args = ('run', '--out', out, '--limit', 4, '--models', 'uniform,lexical')
            first = self.cli(*args)
            self.assertEqual(first.returncode, 0, first.stderr)
            before = (out / 'results.jsonl').read_text()
            self.assertEqual(len(before.splitlines()), 8)
            resumed = self.cli(*args, '--resume')
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual((out / 'results.jsonl').read_text(), before)
            self.assertTrue((out / 'report.md').exists())
            drift = self.cli(*args, '--resume', '--seed', 99)
            self.assertNotEqual(drift.returncode, 0)
            self.assertIn('Resume rejected', drift.stderr)
            overwrite = self.cli(*args)
            self.assertNotEqual(overwrite.returncode, 0)

    def test_http_serialization_and_request_cap(self):
        received = []

        class Opener:
            def open(self, request, timeout):
                body = json.loads(request.data)
                received.append(body)
                self_timeout = timeout
                assert self_timeout == 9
                assert request.headers['Authorization'] == 'Bearer test-only-token'
                return BytesIO(json.dumps(baseline(body, 'uniform')).encode())

        model = {'name': 'local', 'adapter': 'systemone', 'model': 'test/model',
                 'url': 'http://127.0.0.1:12345/v1/systemone', 'key_env': 'BENCH_TEST_KEY'}
        data = load_json(ROOT / 'tests/fixtures/schema-transport-v1.json')
        request = request_for(data, data['cases'][0])
        with patch.dict('os.environ', {'BENCH_TEST_KEY': 'test-only-token'}):
            with patch('urllib.request.build_opener', return_value=Opener()):
                response = call_remote(model, request, 9)
                normalize(response, request)
        self.assertEqual(len(received), 1)
        self.assertEqual(set(received[0]), {'model', 'state', 'questions'})
        self.assertEqual(received[0]['model'], 'test/model')
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'models.json'
            model.pop('key_env')
            config.write_text(json.dumps({'models': [model]}))
            cap = self.cli('run', '--config', config, '--models', 'local', '--out', Path(temp) / 'capped', '--limit', 4, '--max-requests', 2)
            self.assertNotEqual(cap.returncode, 0)
            self.assertIn('exceeding --max-requests', cap.stderr)
            self.assertFalse((Path(temp) / 'capped').exists())

    def test_endpoint_validation(self):
        for url in ('http://example.com/v1', 'https://user:pass@example.com', 'https://example.com?key=secret'):
            with self.assertRaises(ValueError):
                check_url(url)
        check_url('https://inference.baseten.co/v1/systemone')

    def test_cached_catalog_pins_models_and_excludes_aliases(self):
        with tempfile.TemporaryDirectory() as temp:
            raw = Path(temp) / 'catalog.json'
            config = Path(temp) / 'models.json'
            raw.write_text(json.dumps({'data': [
                {'id': 'vendor/model', 'canonical_slug': 'vendor/model-20261030',
                 'architecture': {'output_modalities': ['decisions']}},
                {'id': '~vendor/latest', 'alias_target': {'slug': 'vendor/model'},
                 'architecture': {'output_modalities': ['decisions']}},
                {'id': 'vendor/chat', 'architecture': {'output_modalities': ['text']}}
            ]}))
            result = self.cli('catalog', '--from-file', raw, '--config-out', config)
            self.assertEqual(result.returncode, 0, result.stderr)
            entries = load_json(config)['models']
            self.assertEqual(len(entries), 3)
            self.assertEqual(entries[-1]['model'], 'vendor/model-20261030')

    def test_chat_adapter_json_and_usage(self):
        seen = []

        class Opener:
            def open(self, request, timeout):
                body = json.loads(request.data)
                seen.append(body)
                answer = {'answers': {'decision': {'type': 'noul', 'noul': .8}}}
                raw = {'model': 'chat/pinned', 'choices': [{'message': {'content': json.dumps(answer)}}],
                       'usage': {'prompt_tokens': 100, 'completion_tokens': 40}}
                return BytesIO(json.dumps(raw).encode())

        model = {'adapter': 'chat', 'model': 'chat/requested', 'url': 'https://example.com/v1/chat/completions'}
        data = load_json(ROOT / 'tests/fixtures/schema-transport-v1.json')
        request = request_for(data, data['cases'][1])
        with patch('urllib.request.build_opener', return_value=Opener()):
            raw = call_remote(model, request, 30)
        self.assertEqual(normalize(raw, request)['prediction'], 'yes')
        self.assertEqual(raw['model'], 'chat/pinned')
        self.assertEqual(raw['usage']['prompt_tokens'], 100)
        self.assertEqual(json.loads(seen[0]['messages'][1]['content']), request)


if __name__ == '__main__':
    unittest.main()
