import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('bucket_results',ROOT/'scripts/bucket_results.py')
buckets=importlib.util.module_from_spec(spec);spec.loader.exec_module(buckets)
from decision_bench.core import digest,load_json,request_for


class BucketTests(unittest.TestCase):
    def test_registered_buckets_cover_exact_fixture(self):
        data=load_json(ROOT/'data/shoalrun-expanded-v1.json')
        registration=load_json(ROOT/'analysis/expanded-buckets-v1.json')
        self.assertEqual(registration['fixture_hash'],digest(data))
        self.assertEqual(set(registration['case_tags']),{c['id'] for c in data['cases']})
        result=buckets.analyze(data,registration,[])
        self.assertEqual(result['splits'],{})

    def test_mixed_choice_counts_and_reported_cost(self):
        rows=[{'status':'ok','task':'boolean-result','prediction':'yes','expected':'yes',
               'probabilities':{'yes':.8,'no':.2},'confidence':.8,'latency_ms':10,'group':'a',
               'raw_response':{'usage':{'cost':.001}}},
              {'status':'ok','task':'function-outcome','prediction':'a','expected':'b',
               'probabilities':{'a':.7,'b':.1,'c':.1,'d':.1},'confidence':.7,'latency_ms':20,'group':'b',
               'raw_response':{'usage':{'cost':.003}}}]
        score=buckets.bucket_score(rows)
        self.assertEqual(score['accuracy'],.5)
        self.assertEqual(score['uniform_guess_accuracy'],.375)
        self.assertAlmostEqual(score['chance_adjusted_accuracy'],.2)
        self.assertAlmostEqual(score['reported_cost_per_1000_calls_usd'],2)
        rows[1]['raw_response']={}
        self.assertIsNone(buckets.bucket_score(rows)['reported_cost_per_1000_calls_usd'])


if __name__=='__main__':unittest.main()
