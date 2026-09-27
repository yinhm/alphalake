"""Disclosure checker rejects altered evidence and emits no numerical override."""
import copy
import hashlib
import json
import struct
from unittest.mock import patch

import pytest
from tools.verify_prospectus_coverage import verify


def test_disclosure_evidence_and_tampering(tmp_path):
    pdf = b'%PDF synthetic extraction fixture'
    (tmp_path/'sample.pdf').write_bytes(pdf)
    source = json.dumps([dict(code='688692', period='2023-12-31', observations=[dict(
        field='research_and_development_expense', value=165131648,
        unit='CNY', period_basis='ytd', state='source_observation_not_standard_fact',
        source_evidence=dict(float32_bits=struct.unpack('<I', struct.pack('<f', 165131648))[0]))])]).encode()
    manifest = dict(source_audit_sha256=hashlib.sha256(source).hexdigest(), reviewer='test', reviewed_at='2026-09-27T00:00:00Z', documents=[dict(
        code='688692', announcement_id='fixture', local_path='sample.pdf', pdf_sha256=hashlib.sha256(pdf).hexdigest(),
        cells=[dict(period='2023-12-31', pages=[1], value_page=1, column=1,
                    column_periods=['2023-12-31'], pdf_amount='16513.17', scale=10000,
                    context=['合并利润表', '2023年度', '单位：万元'], note='scope reviewed')])])
    text = '合并利润表 单位：万元 2023年度\n研发费用 16,513.17\n'
    with patch('tools.verify_prospectus_coverage.subprocess.check_output', return_value=text):
        reviews, comparisons = verify(manifest, tmp_path, source)
        assert len(reviews) == 1 and 'value' not in reviews[0] and 'pdf_amount' not in reviews[0]
        assert comparisons[0]['source_value'] == 165131648
        for kind in ('hash', 'amount', 'column', 'scope', 'source'):
            bad = copy.deepcopy(manifest)
            cell = bad['documents'][0]['cells'][0]
            if kind == 'hash':
                bad['documents'][0]['pdf_sha256'] = 'wrong'
            elif kind == 'amount':
                cell['pdf_amount'] = '16513.18'
            elif kind == 'column':
                cell['column_periods'] = ['2022-12-31']
            elif kind == 'scope':
                cell['context'].append('母公司利润表')
            else:
                bad['source_audit_sha256'] = 'wrong'
            with pytest.raises(AssertionError):
                verify(bad, tmp_path, source)
