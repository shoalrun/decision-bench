#!/usr/bin/env python3
"""Deterministic external-curl transport for environments restricting Python networking."""
import argparse
import datetime
import json
import os
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_bench.core import baseline, digest, load_json, normalize, request_for, validate_dataset
from decision_bench.cli import save, verify_oracle
from decision_bench.metrics import summarize, markdown
from decision_bench.transport import preflight


def quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def prepare(args):
    data = validate_dataset(load_json(args.dataset))
    verify_oracle(args.dataset, data)
    config = load_json(args.config)['models']
    names = args.models.split(',')
    models = [next(m for m in config if m['name'] == name) for name in names]
    if len(set(names)) != len(names):
        raise ValueError('Duplicate model names')
    for m in models:
        preflight(m)
        if m['adapter'] not in ('uniform', 'lexical', 'systemone'):
            raise ValueError('External curl batch currently supports System One and offline baselines')
    cases = [c for c in data['cases'] if c['split'] == args.split][:args.limit]
    jobs = [(m, c) for c in cases for m in models]
    random.Random(args.seed).shuffle(jobs)
    remote = sum(m['adapter'] == 'systemone' for m, c in jobs)
    if not cases or remote > args.max_requests:
        raise ValueError('Empty cases or request cap exceeded')
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    private = out / 'transport'
    private.mkdir(mode=0o700)
    metadata = {'dataset_schema': data['schema_version'], 'dataset_hash': digest(data), 'split': args.split,
                'models': models, 'seed': args.seed, 'timeout': args.timeout, 'transport': 'external-curl',
                'planned_results': len(jobs), 'planned_remote_requests': remote,
                'code_hash': digest({str(p.relative_to(ROOT)):p.read_text() for p in
                    [*sorted((ROOT/'decision_bench').glob('*.py')), ROOT/'scripts/curl_batch.py', ROOT/'scripts/known_answers.mjs',
                     *([ROOT/'scripts/expanded_cases.mjs'] if data['schema_version'] in (3,4) else []),
                     *([ROOT/'scripts/presentation_cases.mjs'] if data['schema_version']==4 else [])]}),
                'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if data['schema_version'] in (3,4):
        metadata['expanded_oracle_code_hash'] = digest((ROOT/'scripts/expanded_cases.mjs').read_text())
    if data['schema_version'] == 4:
        metadata['presentation_oracle_code_hash'] = digest((ROOT/'scripts/presentation_cases.mjs').read_text())
    rows, entries, transfers = [], [], []
    for index, (model, case) in enumerate(jobs):
        request = request_for(data, case)
        row = {'model_name': model['name'], 'requested_model': model.get('model', model['adapter']),
               'adapter': model['adapter'], 'case_id': case['id'], 'task': case['task'], 'group': case['group'],
               'expected': case['expected'], 'request_hash': digest(request)}
        if data['schema_version'] == 4:
            row.update(presentation=case['presentation'],base_case_id=case['base_case_id'])
        if model['adapter'] != 'systemone':
            start = time.perf_counter()
            raw = baseline(request, model['adapter'])
            row.update(normalize(raw, request), status='ok', resolved_model=raw.get('model'), raw_response=raw,
                       latency_ms=(time.perf_counter()-start)*1000)
            rows.append(row)
            continue
        body = {'model': model['model'], **request}
        payload = private / ('request-{:03d}.json'.format(index))
        response = private / ('response-{:03d}.json'.format(index))
        save(payload, body)
        headers = ['Content-Type: application/json']
        if model.get('key_env'):
            secret = os.environ[model['key_env']]
            if '\n' in secret or '\r' in secret:
                raise ValueError('Invalid multiline API credential')
            headers.append('Authorization: ' + model.get('auth_scheme','Bearer') + ' ' + secret)
        transfer = ['url = ' + quote(model['url']), 'request = "POST"',
                    'data-binary = ' + quote('@'+str(payload)), 'output = ' + quote(response),
                    'max-time = ' + str(args.timeout), 'connect-timeout = "15"',
                    'write-out = ' + quote('{"job_index":'+str(index)+',"http_status":"%{http_code}","time_total":%{time_total}}\n')]
        transfer.extend('header = ' + quote(header) for header in headers)
        transfers.append('\n'.join(transfer))
        entries.append({'index':index, 'row':row, 'request':request, 'response':str(response)})
    save(out/'manifest.json',metadata)
    save(private/'pending.json', {'entries':entries,'baseline_rows':rows})
    config_path = private/'batch.conf'
    fd = os.open(str(config_path), os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd,'w') as handle:
        handle.write('silent\nshow-error\n'+ '\nnext\n'.join(transfers)+'\n')
    print('Prepared {} fixed requests and {} offline results. Secret curl config mode 0600.'.format(remote,len(rows)))
    print('Config: '+str(config_path))


def collect(args):
    out = Path(args.out).resolve()
    private = out/'transport'
    # Remove the transient credential before parsing or printing any responses.
    (private/'batch.conf').unlink(missing_ok=True)
    if (out/'results.jsonl').exists():
        raise ValueError('Results already collected; refusing to overwrite')
    metadata = load_json(out/'manifest.json')
    pending = load_json(private/'pending.json')
    metrics = {}
    for line in (private/'metrics.jsonl').read_text().splitlines():
        item = json.loads(line)
        item['http_status'] = int(item['http_status'])
        if item['job_index'] in metrics:
            raise ValueError('Duplicate transfer metrics')
        metrics[item['job_index']] = item
    expected_indices = {entry['index'] for entry in pending['entries']}
    if set(metrics) != expected_indices:
        raise ValueError('Incomplete curl batch; missing transfer metrics. No report produced.')
    rows = pending['baseline_rows']
    for entry in pending['entries']:
        row, metric = entry['row'], metrics[entry['index']]
        row['latency_ms'] = metric['time_total']*1000
        if not 200 <= metric['http_status'] < 300:
            row.update(status='error', error_type='HTTPError', http_status=metric['http_status'])
        else:
            try:
                raw = load_json(entry['response'])
                row.update(normalize(raw,entry['request']),status='ok',resolved_model=raw.get('model'),raw_response=raw)
            except (ValueError,KeyError,TypeError,OSError,AttributeError) as error:
                row.update(status='error',error_type=type(error).__name__)
        rows.append(row)
    with (out/'results.jsonl').open('x') as handle:
        for row in rows:
            handle.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
    summary = summarize(rows)
    save(out/'summary.json',summary)
    (out/'report.md').write_text(markdown(summary,metadata))
    metadata['recorded_results'] = len(rows)
    metadata['completed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save(out/'manifest.json',metadata)
    for name, result in summary['models'].items():
        s=result['overall']
        print('{}: N={}, errors={}, accuracy={:.1%}, p50_ms={}'.format(name,s['n'],s['errors'],s['accuracy'],s['p50_ms']))
    print('Report: '+str(out/'report.md'))
    return 2 if any(r['status']=='error' for r in rows) else 0


parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('mode',choices=('prepare','collect'))
parser.add_argument('--out',required=True)
parser.add_argument('--dataset',default='data/shoalrun-known-v1.json')
parser.add_argument('--config',default='models.example.json')
parser.add_argument('--models',help='Explicit comma-separated config entry names; required for prepare')
parser.add_argument('--split',choices=('dev','test'),default='dev')
parser.add_argument('--limit',type=int,default=8)
parser.add_argument('--max-requests',type=int,default=32)
parser.add_argument('--timeout',type=int,default=30)
parser.add_argument('--seed',type=int,default=42)
args=parser.parse_args()
if args.mode == 'prepare' and not args.models:
    parser.error('prepare requires --models with explicit config entry names')
if args.limit<=0 or args.max_requests<0 or args.timeout<=0:
    parser.error('Invalid limit, request cap, or timeout')
try:
    sys.exit(prepare(args) if args.mode=='prepare' else collect(args))
except Exception as error:
    print('Batch failed: '+type(error).__name__+' (credential values omitted)',file=sys.stderr)
    sys.exit(1)
