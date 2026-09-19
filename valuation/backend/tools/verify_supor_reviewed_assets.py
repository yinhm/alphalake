"""核验苏泊尔2026H1镜像原文与已冻结标准事实；不批准或写入估值。"""
from copy import deepcopy
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import re
import struct

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT / 'valuation/research/reviewed-assets-20260917/supor'


def verify(receipt):
    raw = (DIRECTORY / receipt['file']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == receipt['sha256'], 'PDF hash differs'
    reader = PdfReader(DIRECTORY / receipt['file'])
    assert len(reader.pages) == receipt['pages'] == 133
    pages = {n: re.sub(r'\s+', '', reader.pages[n - 1].extract_text()) for n in (86, 87)}
    assert all('浙江苏泊尔股份有限公司2026年半年度报告全文' in t for t in pages.values())
    a = receipt['amounts_cny']
    money = lambda key: format(Decimal(a[key]), ',.2f')
    assert '合计' + money('current_total') in pages[86]
    for page, key, label in ((86, 'current_restricted', '一年内到期的其他债权投资'),
                             (87, 'noncurrent_restricted', '其他债权投资')):
        assert ('于2026年6月30日，使用受限的' + label + '为' + money(key)
                + '元，系银行承兑汇票质押的大额存单') in pages[page], key
    assert money('combined_total') in pages[87]
    assert money('noncurrent_total') in pages[87]
    assert '减：一年内到期的部分' in pages[87]
    assert Decimal(a['combined_total']) - Decimal(a['current_total']) == Decimal(a['noncurrent_total'])
    raw_request = (ROOT / receipt['request_snapshot']).read_bytes()
    assert hashlib.sha256(raw_request).hexdigest() == receipt['request_sha256']
    data = json.loads(gzip.decompress(raw_request))['data']
    assert data['code'] == receipt['code'] == '002032'
    assert data['report_period'] == receipt['period'] == '2026-06-30'
    results = []
    for field, total, restricted in (('noncurrent_assets_due_within_one_year', 'current_total', 'current_restricted'),
                                      ('other_debt_investments', 'noncurrent_total', 'noncurrent_restricted')):
        facts = [f for f in data['facts'] if f['canonical_field'] == field and f['period'] == receipt['period']]
        assert len(facts) == 1
        fact = facts[0]
        assert fact['announcement_id'] == receipt['canonical_announcement_id']
        assert fact['pdf_url'] == receipt['canonical_url']
        value = Decimal(a[total]) / Decimal(fact['multiplier'])
        assert struct.unpack('<I', struct.pack('<f', float(value)))[0] == fact['bits'], field
        assert Decimal(a[restricted]) <= Decimal(a[total])
        results.append({'field': field, 'standard_total_cny': fact['value'],
                        'pdf_total_cny': a[total], 'restricted_cny': a[restricted]})
    return {'pdf_amounts_checked': 5, 'source_bits_checked': 2, 'components': results,
            'valuation_addback': None, 'retrieval_snapshot_status': receipt['production_status']}


if __name__ == '__main__':
    receipt = json.loads((DIRECTORY / 'receipt.json').read_bytes())
    result = verify(receipt)
    # 锁定本期受限额，不能误用比较列或把受限额归零。
    for key in receipt['amounts_cny']:
        changed = deepcopy(receipt)
        changed['amounts_cny'][key] = str(Decimal(changed['amounts_cny'][key]) + Decimal('.01'))
        try:
            verify(changed)
        except AssertionError:
            continue
        raise AssertionError(f'tampering accepted: {key}')
    result['amount_tampering_rejected'] = 5
    print(json.dumps(result, ensure_ascii=False, indent=2))
