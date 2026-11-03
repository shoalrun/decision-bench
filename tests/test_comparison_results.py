import unittest
from scripts.comparison_results import merge_retry


def row(status='error',attempt=1):
    return dict(model_name='model',case_id='case',expected='yes',task='function-validity',group='family',request_hash='same-request',status=status,attempt=attempt)


class RetryEvidenceTests(unittest.TestCase):
    def test_wrong_valid_answer_cannot_be_replaced(self):
        original=dict(row('ok'),prediction='no')
        with self.assertRaisesRegex(ValueError,'valid'):merge_retry(original,dict(row('ok',2),prediction='yes'))

    def test_retry_must_keep_request_and_answer(self):
        for key in ('request_hash','expected','model_name','case_id','requested_model','adapter','presentation','base_case_id'):
            changed=dict(row('ok',2));changed[key]='changed'
            with self.assertRaisesRegex(ValueError,'identity'):merge_retry(row(),changed)

    def test_retry_chain_cannot_skip_an_attempt(self):
        with self.assertRaisesRegex(ValueError,'consecutive'):merge_retry(row(),row('ok',3))
        second=merge_retry(row(),row('error',2))
        self.assertEqual(merge_retry(second,row('ok',3))['attempt'],3)

    def test_success_does_not_mutate_first_evidence(self):
        original=row();saved=dict(original)
        result=merge_retry(original,row('ok',2))
        self.assertEqual(original,saved)
        self.assertEqual(result['status'],'ok')
