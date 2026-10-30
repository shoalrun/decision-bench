import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from decision_bench.core import baseline, load_json, normalize, request_for, validate_dataset

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'data/shoalrun-presentations-v1.json'


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.data=load_json(FIXTURE)

    def test_all_matched_triplets_and_focused_code_replay(self):
        validate_dataset(self.data)
        self.assertEqual(len(self.data['cases']),1200)
        result=subprocess.run(['node','scripts/presentation_cases.mjs','verify',str(FIXTURE)],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Verified 400 matched triplets',result.stdout)

    def test_only_reference_body_changes_between_views(self):
        groups={}
        for case in self.data['cases']:
            request=request_for(self.data,case)
            poisoned=copy.deepcopy(case)
            poisoned.update(id='PRIVATE_ID',expected='PRIVATE_GOLD',oracle={'PRIVATE_ORACLE':True},split='PRIVATE_SPLIT',group='PRIVATE_GROUP',rationale='PRIVATE_REASON',history=['PRIVATE_HISTORY'])
            self.assertEqual(request,request_for(self.data,poisoned))
            self.assertNotIn('PRIVATE_',json.dumps(request))
            self.assertNotIn('presentation',request['state'])
            normalize(baseline(request,'uniform'),request)
            body=request['state']['passages'][0].pop('text')
            groups.setdefault(case['base_case_id'],[]).append((case['presentation'],request,body))
        for variants in groups.values():
            self.assertEqual(len(variants),3)
            self.assertEqual(variants[0][1],variants[1][1])
            self.assertEqual(variants[1][1],variants[2][1])
            self.assertEqual(len({v[2] for v in variants}),3)

    def test_paired_input_or_expected_answer_cannot_change(self):
        changed=copy.deepcopy(self.data)
        changed['cases'][0]['expected']='tampered'
        with self.assertRaisesRegex(ValueError,'Presentation changed'):
            validate_dataset(changed)
        changed=copy.deepcopy(self.data)
        changed['cases'][0]['input']['args']=['tampered']
        with self.assertRaisesRegex(ValueError,'Presentation changed'):
            validate_dataset(changed)

    def test_focused_code_is_checked_for_behavior_not_just_hash(self):
        changed=copy.deepcopy(self.data)
        ref=changed['focused_sources']['phraseMatches']
        self.assertIn('return new RegExp',ref['text'])
        ref['text']=ref['text'].replace('return new RegExp','return false && new RegExp')
        ref['sha256']=hashlib.sha256(ref['text'].encode()).hexdigest()
        validate_dataset(changed)
        with tempfile.TemporaryDirectory() as temp:
            fixture=Path(temp)/'tampered.json';fixture.write_text(json.dumps(changed))
            result=subprocess.run(['node','scripts/presentation_cases.mjs','verify',str(fixture)],cwd=ROOT,text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Focused/full behavior differs',result.stderr)

    def test_cli_preserves_presentation_breakdown(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'run'
            result=subprocess.run([sys.executable,'-m','decision_bench','run','--dataset',str(FIXTURE),'--models','uniform','--limit','15','--out',str(out)],cwd=ROOT,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            summary=load_json(out/'summary.json')['models']['uniform']
            self.assertEqual(set(summary['presentations']),{'full','focused','rules'})
            self.assertEqual({s['n'] for s in summary['presentations'].values()},{5})
            rows=[json.loads(line) for line in (out/'results.jsonl').read_text().splitlines()]
            self.assertTrue(all('base_case_id' in row and 'presentation' in row for row in rows))

    def test_external_transport_retains_view_and_oracle_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'batch'
            result=subprocess.run([sys.executable,'scripts/curl_batch.py','prepare','--dataset',str(FIXTURE),'--models','uniform','--limit','3','--max-requests','0','--out',str(out)],cwd=ROOT,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            manifest=load_json(out/'manifest.json')
            self.assertIn('presentation_oracle_code_hash',manifest)
            self.assertIn('expanded_oracle_code_hash',manifest)
            pending=load_json(out/'transport/pending.json')
            self.assertEqual({r['presentation'] for r in pending['baseline_rows']},{'full','focused','rules'})


if __name__=='__main__':unittest.main()
