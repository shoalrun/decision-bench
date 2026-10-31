import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from decision_bench.core import baseline, load_json
ROOT=Path(__file__).resolve().parents[1]


class CurlBatchTests(unittest.TestCase):
    def test_prepare_requires_explicit_model_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'run'
            result=subprocess.run([sys.executable,'scripts/curl_batch.py','prepare','--out',str(out)],cwd=ROOT,text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('prepare requires --models',result.stderr)
            self.assertFalse(out.exists())

    def test_prepare_payload_separation_and_collect(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'run'
            env={**os.environ,'OPENROUTER_API_KEY':'TEST_ONLY_SECRET'}
            prepared=subprocess.run([sys.executable,'scripts/curl_batch.py','prepare','--config','models/openrouter.example.json','--models','uniform,joey-openrouter,mammoth-openrouter,elephant-openrouter,jev-latest','--out',str(out),'--limit','1','--max-requests','4'],cwd=ROOT,env=env,text=True,capture_output=True)
            self.assertEqual(prepared.returncode,0,prepared.stderr)
            self.assertNotIn('TEST_ONLY_SECRET',prepared.stdout+prepared.stderr)
            private=out/'transport'
            config=private/'batch.conf'
            self.assertEqual(config.stat().st_mode & 0o777,0o600)
            pending=load_json(private/'pending.json')
            self.assertEqual(len(pending['entries']),4)
            manifest=load_json(out/'manifest.json')
            self.assertEqual(set(manifest['case_ids']),{r['row']['case_id'] for r in pending['entries']})
            self.assertTrue(all(manifest['request_hashes'][r['row']['case_id']]==r['row']['request_hash'] for r in pending['entries']))
            lines=[]
            for entry in pending['entries']:
                request_file=private/('request-{:03d}.json'.format(entry['index']))
                payload=load_json(request_file)
                self.assertEqual(set(payload),{'model','state','questions'})
                self.assertEqual(payload['state'],entry['request']['state'])
                self.assertNotIn('TEST_ONLY_SECRET',request_file.read_text())
                Path(entry['response']).write_text(json.dumps(baseline(entry['request'],'uniform')))
                lines.append(json.dumps({'job_index':entry['index'],'http_status':'200','time_total':.1}))
            (private/'metrics.jsonl').write_text('\n'.join(lines)+'\n')
            collected=subprocess.run([sys.executable,'scripts/curl_batch.py','collect','--out',str(out)],cwd=ROOT,text=True,capture_output=True)
            self.assertEqual(collected.returncode,0,collected.stderr)
            self.assertFalse(config.exists())
            self.assertEqual(len((out/'results.jsonl').read_text().splitlines()),5)
            self.assertTrue((out/'report.md').exists())


if __name__=='__main__':
    unittest.main()
