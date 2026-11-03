#!/usr/bin/env python3
"""Verify a fixed comparison and ordered retry passes; export sanitized aggregates."""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from decision_bench.core import digest,load_json,normalize,request_for,validate_dataset
from decision_bench.metrics import score
from scripts.presentation_results import paired


def merge_retry(current,retry):
    """Only an unavailable response may be replaced; a wrong valid answer is retained."""
    if current['status']=='ok':raise ValueError('Cannot retry a valid response')
    for key in ('model_name','case_id','expected','task','group','request_hash','requested_model','adapter','presentation','base_case_id'):
        if current.get(key)!=retry.get(key):raise ValueError('Retry identity mismatch: '+key)
    if retry.get('attempt',0)!=current.get('attempt',1)+1:raise ValueError('Retry attempts must be consecutive')
    return retry


def cost_stats(rows):
    values=[r.get('raw_response',{}).get('usage',{}).get('cost') for r in rows]
    good=[v for v in values if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>=0]
    return {'known_cost_usd':sum(good),'covered_attempts':len(good),'unknown_cost_attempts':len(rows)-len(good)}


def quality(rows):
    s=score(rows)
    s.pop('p50_ms',None);s.pop('p95_ms',None)
    s['correct']=sum(r['status']=='ok' and r.get('prediction')==r['expected'] for r in rows)
    c=cost_stats(rows);s.update(c)
    s['known_usage_per_1000_completed_cases_usd']=c['known_cost_usd']/len(rows)*1000 if c['covered_attempts']==len(rows) else None
    s['binary_prediction_counts']=dict(collections.Counter(r.get('prediction','error') for r in rows if r.get('_type')=='noul'))
    return s


def file_hash(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def analyze(plan_path,retry_dirs):
    plan=load_json(plan_path);config=load_json(plan['config']);models={m['name']:m for m in config['models']};names=[m['name'] for m in plan['models']]
    assert len(names)==8 and set(names)==set(models)-{'uniform'}
    if file_hash(plan['config'])!=plan['config_sha256']:raise ValueError('Changed model config')
    data_cache={};first={};current={};source_meta=[];all_attempts=[];retry_meta=[]
    for run in plan['runs']:
        source=run['out'];p=Path(source);manifest=load_json(p/'manifest.json');rows=[json.loads(s) for s in (p/'results.jsonl').read_text().splitlines()]
        if not manifest.get('completed_at') or len(rows)!=manifest['planned_results'] or len(rows)!=manifest['recorded_results']:raise ValueError('Incomplete source run')
        if manifest['models']!=config['models'] or manifest['seed']!=42 or manifest['timeout']!=30 or manifest['python']!='3.9.6' or manifest['platform']!='Darwin':raise ValueError('Different execution settings')
        path=run['dataset']
        if path not in data_cache:data_cache[path]=validate_dataset(load_json(path))
        data=data_cache[path];cases={c['id']:c for c in data['cases'] if c['split']==run['split']}
        if manifest['dataset_hash']!=digest(data) or set(manifest['case_ids'])!=set(cases):raise ValueError('Fixture/selection mismatch')
        expected={(n,c) for n in models for c in cases};actual={(r['model_name'],r['case_id']) for r in rows}
        if actual!=expected or len(actual)!=len(rows):raise ValueError('Missing/duplicate source rows')
        cohort='presentations' if data['schema_version']==4 else 'expanded' if data['schema_version']==3 else 'retrieval'
        for r in rows:
            c=cases[r['case_id']];request=request_for(data,c)
            if r['expected']!=c['expected'] or r['request_hash']!=digest(request) or r['request_hash']!=manifest['request_hashes'][c['id']]:raise ValueError('Source request/answer mismatch')
            if any(r.get(k)!=c.get(k) for k in ('task','group','presentation','base_case_id')):raise ValueError('Source case metadata mismatch')
            if r.get('adapter')!=models[r['model_name']]['adapter'] or r.get('requested_model')!=models[r['model_name']].get('model',models[r['model_name']]['adapter']):raise ValueError('Source model identity mismatch')
            if r['model_name']=='uniform':continue
            if r['status']=='ok':
                normalized=normalize(r['raw_response'],request)
                if any(r[k]!=normalized[k] for k in ('prediction','probabilities','confidence')) or r['resolved_model']!=models[r['model_name']]['model']:raise ValueError('Invalid normalized source response/version')
            r={**r,'_source_run':source,'_cohort':cohort,'_split':run['split'],'_type':request['questions']['decision']['type'],'_function':c.get('input',{}).get('function'),'_case':c,'_request':request}
            key=(source,r['model_name'],r['case_id']);first[key]=r;current[key]=r;all_attempts.append(r)
        source_meta.append({'cohort':cohort,'split':run['split'],'manifest_hash':digest(manifest),'results_sha256':file_hash(p/'results.jsonl'),'fixture_hash':digest(data),'request_set_hash':digest(manifest['request_hashes']),**{k:manifest[k] for k in ('code_hash','started_at','completed_at','seed','timeout','python','platform')}})
    if len(first)!=13568:raise ValueError('Unexpected first-pass call count')
    for directory in retry_dirs:
        p=Path(directory);meta=load_json(p/'manifest.json');rows=[json.loads(s) for s in (p/'results.jsonl').read_text().splitlines()]
        if not meta.get('completed_at') or len(rows)!=meta['recorded_results']:raise ValueError('Incomplete retry pass')
        if meta.get('python')!='3.9.6' or meta.get('platform')!='Darwin' or meta.get('timeout_seconds')!=30 or meta.get('transport_sha256')!=plan['transport_sha256']:raise ValueError('Retry execution settings mismatch')
        if 'sources' in meta:
            for source in meta['sources']:
                path=Path(source['run'])
                if source['manifest_hash']!=digest(load_json(path/'manifest.json')) or source['results_sha256']!=file_hash(path/'results.jsonl'):raise ValueError('Retry source evidence changed')
        if 'source_retry_manifest_hash' in meta:
            previous=Path(retry_dirs[retry_dirs.index(directory)-1])
            if meta['source_retry_manifest_hash']!=digest(load_json(previous/'manifest.json')) or meta['source_retry_results_sha256']!=file_hash(previous/'results.jsonl'):raise ValueError('Prior retry evidence changed')
        failures={k for k,r in current.items() if r['status']!='ok'};seen=set()
        for r in rows:
            key=(r['source_run'],r['model_name'],r['case_id'])
            if key in seen or key not in failures:raise ValueError('Duplicate or non-failed retry')
            seen.add(key);old=current[key];merge_retry(old,r)
            if r['status']=='ok':
                normalized=normalize(r['raw_response'],old['_request'])
                if any(r[k]!=normalized[k] for k in ('prediction','probabilities','confidence')) or r['resolved_model']!=models[r['model_name']]['model']:raise ValueError('Invalid retry response/version')
            merged={**old,**r};current[key]=merged;all_attempts.append(merged)
        if seen!=failures:raise ValueError('Retry pass must cover exactly current failures')
        retry_meta.append({'attempt':rows[0]['attempt'],'calls':len(rows),'valid':sum(r['status']=='ok' for r in rows),'manifest_hash':digest(meta),'results_sha256':file_hash(p/'results.jsonl'),'started_at':meta['started_at'],'completed_at':meta['completed_at'],'pause_seconds':meta.get('pause_seconds',0)})
    tags=load_json('analysis/expanded-buckets-v1.json')
    expanded=next(d for d in data_cache.values() if d['schema_version']==3)
    if tags['fixture_hash']!=digest(expanded):raise ValueError('Bucket registration mismatch')
    out={'analyzer_sha256':file_hash(__file__),'evaluation_date':plan['report_date'],'scope':'Eight-model code-behavior comparison on previously published cases. Complete first pass plus separately recorded retries of HTTP failures only. No valid response replaced.','models':{n:models[n]['model'] for n in names},'first_pass_calls':len(first),'retry_calls':len(all_attempts)-len(first),'all_attempts':len(all_attempts),'valid_responses':sum(r['status']=='ok' for r in all_attempts),'final_decision_slots':len(current),'final_unavailable':sum(r['status']!='ok' for r in current.values()),'costs':cost_stats(all_attempts),'sources':source_meta,'retry_passes':retry_meta,'cohorts':{},'reliability':{},'metric_notes':{'accuracy':'Primary quality uses original valid response, otherwise ordered retry. Failures count wrong.','latency':'First-pass valid responses only; excludes failed requests and all retries.','cost':'Known provider usage only; failed-attempt cost is unavailable. Per1000completedcase cost excludes unknown failure charges.','retries':'One318-call pass with3secondspacing, then one explicitly authorized third attempt. No answer-based retries.'}}
    for n in names:
        original=[r for r in first.values() if r['model_name']==n];attempts=[r for r in all_attempts if r['model_name']==n];final=[r for r in current.values() if r['model_name']==n]
        out['reliability'][n]={'first_errors':sum(r['status']!='ok' for r in original),'first_calls':len(original),'retry_calls':len(attempts)-len(original),'final_errors':sum(r['status']!='ok' for r in final),'all_attempt_costs':cost_stats(attempts),'http_errors':dict(collections.Counter(r.get('http_status',r.get('error_type')) for r in attempts if r['status']!='ok'))}
    for cohort in ('retrieval','expanded','presentations'):
        out['cohorts'][cohort]={}
        for split in ('dev','test'):
            entries={}
            for n in names:
                rows=[r for r in current.values() if r['_cohort']==cohort and r['_split']==split and r['model_name']==n];original=[r for r in first.values() if r['_cohort']==cohort and r['_split']==split and r['model_name']==n]
                entry={'after_retries':quality(rows),'first_pass':score(original),'types':{t:quality([r for r in rows if r['_type']==t]) for t in ('noul','choice')}}
                if cohort!='retrieval':
                    entry['functions']={f:quality([r for r in rows if r['_function']==f]) for f in sorted({r['_function'] for r in rows})}
                    entry['complexity']={label:quality([r for r in rows if tags['case_tags'][r.get('base_case_id',r['case_id'])]['complexity']==label]) for label in ('low','medium','high')}
                if cohort=='presentations':
                    entry['views']={v:{'after_retries':quality([r for r in rows if r['presentation']==v]),'first_pass':score([r for r in original if r['presentation']==v]),'functions':{f:quality([r for r in rows if r['presentation']==v and r['_function']==f]) for f in sorted({r['_function'] for r in rows})}} for v in ('full','focused','rules')}
                    entry['paired']={a+'_to_'+b:paired([r for r in rows if r['presentation']==a],[r for r in rows if r['presentation']==b]) for a,b in [('full','focused'),('full','rules'),('focused','rules')]}
                entries[n]=entry
            out['cohorts'][cohort][split]=entries
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True);p.add_argument('--retry-runs',nargs='*',default=[]);p.add_argument('--out',required=True);a=p.parse_args()
    out=Path(a.out)
    if out.exists():raise SystemExit('Output exists; choose a new output file.')
    result=analyze(a.plan,a.retry_runs);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2)+'\n')
    print('Verified',result['all_attempts'],'attempts;',result['final_decision_slots'],'final decisions;',result['final_unavailable'],'unavailable.')
    print('Known cost:',result['costs'])


if __name__=='__main__':main()
