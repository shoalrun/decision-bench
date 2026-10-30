#!/usr/bin/env python3
"""Verify completed presentation runs and summarize matched reference comparisons."""
import argparse
import collections
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_bench.core import digest, load_json, request_for, validate_dataset
from scripts.bucket_results import bucket_score

VIEWS = ('full', 'focused', 'rules')


def measured(rows):
    result = bucket_score(rows)
    for field in ('input_tokens', 'output_tokens'):
        values = [r.get('raw_response', {}).get('usage', {}).get(field) for r in rows]
        known = [v for v in values if isinstance(v, int) and not isinstance(v, bool) and v >= 0]
        result[field + '_covered_rows'] = len(known)
        result['mean_' + field] = sum(known) / len(known) if len(known) == len(rows) and known else None
    return result


def paired(left, right):
    """Pair every base case; errors count as wrong and remain separately visible."""
    a = {r['base_case_id']: r for r in left}
    b = {r['base_case_id']: r for r in right}
    if len(a) != len(left) or len(b) != len(right) or a.keys() != b.keys():
        raise ValueError('Pairing requires unique, identical base case sets')
    result = dict(n=len(a), both_correct=0, improved=0, regressed=0, both_wrong=0,
                  changed_prediction=0, left_errors=0, right_errors=0)
    for key in a:
        x, y = a[key], b[key]
        if x['expected'] != y['expected']:
            raise ValueError('Paired answers differ')
        ac = x['status'] == 'ok' and x.get('prediction') == x['expected']
        bc = y['status'] == 'ok' and y.get('prediction') == y['expected']
        result['both_correct' if ac and bc else 'improved' if bc else 'regressed' if ac else 'both_wrong'] += 1
        result['changed_prediction'] += x.get('prediction') != y.get('prediction')
        result['left_errors'] += x['status'] != 'ok'
        result['right_errors'] += y['status'] != 'ok'
    result['net_correct_change'] = result['improved'] - result['regressed']
    return result


def analyze(data, registration, run_dirs):
    if digest(data['base_fixture']) != registration['fixture_hash']:
        raise ValueError('Wrong bucket registration')
    cases = {c['id']: c for c in data['cases']}
    tags = registration['case_tags']
    if set(tags) != {c['base_case_id'] for c in cases.values()}:
        raise ValueError('Bucket coverage mismatch')
    requests = {key: digest(request_for(data, case)) for key, case in cases.items()}
    output = {'fixture_hash': digest(data), 'base_fixture_hash': digest(data['base_fixture']),
              'registration_hash': digest(registration), 'unique_base_cases': len(tags),
              'interpretation': 'Matched views of observed cases; not independent new questions or a pure language-only experiment.',
              'splits': {}, 'runs': []}
    model_configs = None
    code_hash = None
    for directory in run_dirs:
        root = Path(directory)
        manifest = load_json(root / 'manifest.json')
        split = manifest['split']
        if split in output['splits']:
            raise ValueError('Analyze repeat runs separately')
        if not manifest.get('completed_at') or manifest['dataset_hash'] != digest(data):
            raise ValueError('Incomplete run or fixture mismatch')
        if model_configs is not None and (manifest['models'] != model_configs or manifest['code_hash'] != code_hash):
            raise ValueError('Run model/code settings differ')
        model_configs, code_hash = manifest['models'], manifest['code_hash']
        ids = {key for key, c in cases.items() if c['split'] == split}
        models = {m['name'] for m in manifest['models']}
        if len(models) != len(manifest['models']) or set(manifest['case_ids']) != ids or len(manifest['case_ids']) != len(ids):
            raise ValueError('Run selection mismatch')
        rows = [json.loads(line) for line in (root / 'results.jsonl').read_text().splitlines()]
        expected = {(m, key) for m in models for key in ids}
        actual = {(r['model_name'], r['case_id']) for r in rows}
        if (len(rows) != len(expected) or actual != expected or
                manifest['planned_results'] != len(rows) or manifest.get('recorded_results') != len(rows)):
            raise ValueError('Missing, extra, or duplicate result rows')
        for row in rows:
            case = cases[row['case_id']]
            if any(row[k] != case[k] for k in ('expected', 'task', 'group', 'base_case_id', 'presentation')):
                raise ValueError('Row identity mismatch')
            if row['request_hash'] != requests[case['id']] or manifest['request_hashes'][case['id']] != requests[case['id']]:
                raise ValueError('Request identity mismatch')
        split_result = {'models': {}}
        for model in sorted(models):
            by_view = {v: [r for r in rows if r['model_name'] == model and r['presentation'] == v] for v in VIEWS}
            entry = {'views': {v: measured(rr) for v, rr in by_view.items()}, 'paired': {}, 'axes': {}}
            for a, b in itertools.combinations(VIEWS, 2):
                entry['paired'][a + '_to_' + b] = paired(by_view[a], by_view[b])
            for axis in ('function', 'complexity', 'family'):
                grouped = collections.defaultdict(lambda: collections.defaultdict(list))
                for v, rr in by_view.items():
                    for row in rr:
                        label = row['group'] if axis == 'family' else tags[row['base_case_id']][axis]
                        grouped[label][v].append(row)
                entry['axes'][axis] = {k: {v: measured(rr) for v, rr in vv.items()} for k, vv in sorted(grouped.items())}
            split_result['models'][model] = entry
        output['splits'][split] = split_result
        output['runs'].append({k: manifest[k] for k in ('split', 'code_hash', 'started_at', 'completed_at', 'seed', 'timeout', 'planned_remote_requests')})
        output['runs'][-1].update(manifest_hash=digest(manifest), models=manifest['models'], recorded_results=len(rows))
    return output


def markdown(result):
    lines = ['# Matched reference-format comparison', '',
             'Each view repeats the same underlying cases. Errors count as wrong. Improvements and regressions pair identical base cases.', '']
    for split, value in result['splits'].items():
        lines += ['## ' + split, '', '| Model | View | Correct / N | Errors | Median ms | Cost / 1,000 calls | Mean input tokens |', '|---|---|---:|---:|---:|---:|---:|']
        for model, entry in value['models'].items():
            for view, s in entry['views'].items():
                cost = s['reported_cost_per_1000_calls_usd']
                lines.append('| {} | {} | {}/{} | {} | {:.0f} | {} | {} |'.format(model, view, round(s['accuracy'] * s['n']), s['n'], s['errors'], s['p50_ms'], '—' if cost is None else '${:.5f}'.format(cost), s['mean_input_tokens']))
        lines += ['', '| Model | Change | Improved | Regressed | Both correct | Both wrong |', '|---|---|---:|---:|---:|---:|']
        for model, entry in value['models'].items():
            for pair, s in entry['paired'].items():
                lines.append('| {} | {} | {} | {} | {} | {} |'.format(model, pair, s['improved'], s['regressed'], s['both_correct'], s['both_wrong']))
        lines += ['']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', nargs='+', required=True)
    parser.add_argument('--dataset', default='data/shoalrun-presentations-v1.json')
    parser.add_argument('--buckets', default='analysis/expanded-buckets-v1.json')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    result = analyze(validate_dataset(load_json(args.dataset)), load_json(args.buckets), args.runs)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'presentation-summary.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'report.md').write_text(markdown(result))
    print('Verified presentation analysis:', out / 'report.md')


if __name__ == '__main__':
    main()
