#!/usr/bin/env python3
"""Analyze completed runs using separately registered, input-based case buckets."""
import argparse
import collections
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from decision_bench.core import digest,load_json,request_for,validate_dataset
from decision_bench.metrics import score


def bucket_score(rows):
    result=score(rows)
    if not rows:return result
    chance=sum(.25 if r['task']=='function-outcome' else .5 for r in rows)/len(rows)
    result['uniform_guess_accuracy']=chance
    result['chance_adjusted_accuracy']=(result['accuracy']-chance)/(1-chance)
    known=[]
    for row in rows:
        value=row.get('raw_response',{}).get('usage',{}).get('cost')
        if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0:
            known.append(value)
    result['reported_cost_covered_rows']=len(known)
    result['reported_cost_sum_usd']=sum(known) if known else None
    result['reported_cost_per_1000_calls_usd']=sum(known)/len(known)*1000 if len(known)==len(rows) else None
    result['resolved_models']=sorted({r.get('resolved_model') for r in rows if r.get('resolved_model')})
    result['family_count']=len({r['group'] for r in rows})
    binary=[r for r in rows if r['task']!='function-outcome']
    result['binary_prediction_counts']=dict(collections.Counter(r.get('prediction','error') for r in binary))
    result['binary_always_no_accuracy']=sum(r['expected']=='no' for r in binary)/len(binary) if binary else None
    return result


def analyze(data,registration,rows):
    if registration['fixture_hash']!=digest(data):raise ValueError('Bucket registration is for another fixture')
    cases={c['id']:c for c in data['cases']}
    tags=registration['case_tags']
    if set(cases)!=set(tags):raise ValueError('Bucket tags must cover the exact fixture case set')
    seen=set()
    for row in rows:
        key=(row['model_name'],row['case_id'])
        if key in seen:raise ValueError('Duplicate model/case rows; analyze repeat runs separately')
        seen.add(key)
        case=cases[row['case_id']]
        if row['expected']!=case['expected'] or row['task']!=case['task'] or row['request_hash']!=digest(request_for(data,case)):
            raise ValueError('Result does not match frozen case and request')
        if tags[case['id']]['function']!=case['input']['function']:raise ValueError('Invalid function bucket')
    result={'fixture_hash':digest(data),'registration_hash':digest(registration),'splits':{}}
    for split in ('dev','test'):
        selected=[r for r in rows if cases[r['case_id']]['split']==split]
        if not selected:continue
        groups={}
        for axis in ('complexity','ask_type','function','family','edges'):
            grouped=collections.defaultdict(list)
            for row in selected:
                value=row['group'] if axis=='family' else tags[row['case_id']][axis]
                labels=value if axis=='edges' else [value]
                for label in labels:grouped[(label,row['model_name'])].append(row)
            groups[axis]={}
            for (label,model),values in sorted(grouped.items()):
                groups[axis].setdefault(label,{})[model]=bucket_score(values)
        result['splits'][split]={'overall':{m:bucket_score([r for r in selected if r['model_name']==m]) for m in sorted({r['model_name'] for r in selected})},'axes':groups}
    return result


def text_number(value,digits=1):
    return '—' if value is None else ('{:.%df}'%digits).format(value)


def markdown(result,registration):
    lines=['# Decision model performance by task and complexity','',
           'Complexity labels were assigned from source rules and input families before inspecting expanded model results. '
           'They are structural estimates, not measured model difficulty. Task type and source length are correlated with complexity; '
           'inspect the function and task breakdowns before attributing a difference to complexity.','',
           'Guessing baseline assumes uniform random choice: 50% for yes/no and 25% for four-choice cases. '
           'Adjusted accuracy is (accuracy − guessing baseline) / (1 − guessing baseline). Errors count as wrong. '
           'Costs are provider-reported usage; missing costs remain unknown. Timings are client round trips.','']
    for name,definition in registration['complexity_definitions'].items():lines.append('- **'+name+'**: '+definition)
    for split,entry in result['splits'].items():
        lines.extend(['','# '+split.title(),''])
        for axis in ('complexity','ask_type','function'):
            lines.extend(['## '+axis.replace('_',' ').title(),'',
                          '| Bucket | Model | N | Errors | Correct | Accuracy | Guessing baseline | Adjusted accuracy | p50 ms | Reported $/1k calls |',
                          '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|'])
            for bucket,models in entry['axes'][axis].items():
                for model,s in models.items():
                    lines.append('| {} | {} | {} | {} | {} | {:.1%} | {:.1%} | {:.1%} | {} | {} |'.format(
                        bucket,model,s['n'],s['errors'],round(s['accuracy']*s['n']),s['accuracy'],s['uniform_guess_accuracy'],
                        s['chance_adjusted_accuracy'],text_number(s['p50_ms']),text_number(s['reported_cost_per_1000_calls_usd'],5)))
    lines.extend(['','Related cases share templates; counts do not represent independent observations. '
                  'Edge-condition buckets overlap and must not be summed. Full family, edge, threshold, probability-error, '
                  'resolved-version and cost-coverage details are in bucket-summary.json.',''])
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs',nargs='+',required=True)
    p.add_argument('--dataset',default='data/shoalrun-expanded-v1.json')
    p.add_argument('--buckets',default='analysis/expanded-buckets-v1.json')
    p.add_argument('--out',required=True)
    args=p.parse_args()
    data=validate_dataset(load_json(args.dataset));registration=load_json(args.buckets)
    rows=[];provenance=[]
    for directory in args.runs:
        root=Path(directory);manifest=load_json(root/'manifest.json')
        if not manifest.get('completed_at'):raise ValueError('Run is not complete: '+directory)
        if manifest['dataset_hash']!=digest(data):raise ValueError('Run fixture mismatch')
        rr=[json.loads(line) for line in (root/'results.jsonl').read_text().splitlines()]
        if len(rr)!=manifest['planned_results'] or manifest.get('recorded_results')!=len(rr):raise ValueError('Incomplete result count')
        rows.extend(rr)
        provenance.append({'directory':directory,'manifest_hash':digest(manifest),'result_rows':len(rr),'code_hash':manifest['code_hash']})
    result=analyze(data,registration,rows);result['runs']=provenance
    out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
    (out/'bucket-summary.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'report.md').write_text(markdown(result,registration))
    print('Bucket analysis: '+str(out/'report.md'))


if __name__=='__main__':main()
