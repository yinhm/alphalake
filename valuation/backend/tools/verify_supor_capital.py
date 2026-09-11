"""苏泊尔三年已识别金融资产扣减与负债比较列核查，不计算完整营运资本。"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import struct

from pypdf import PdfReader

DIRECTORY=Path('valuation/research/continuing-operations-five')


def verify(ledger, pdf_directory):
    plan=json.loads((DIRECTORY/'supor-capital-plan.json').read_bytes())
    raw=(DIRECTORY/'capital-snapshot.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==plan['source_snapshot_sha256']
    raw_documents=Path('valuation/research/management-targets-five/documents.json').read_bytes()
    assert hashlib.sha256(raw_documents).hexdigest()==plan['documents_sha256']
    documents=json.loads(raw_documents)['documents']
    source=json.loads(raw)['records']
    assert [r['year'] for r in ledger['reports']]==plan['report_years']
    results=[]; amounts_checked=0; bits_checked=0
    for report in ledger['reports']:
        year=report['year'];doc=report['document']
        assert doc==next(d for d in documents if d['code']=='002032' and d['report_year']==year)
        path=pdf_directory/doc['file']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==doc['sha256']
        reader=PdfReader(path)
        record=[r for r in source if r['code']=='002032' and r['period']==f'{year}-12-31']
        assert len(record)==1
        base=['current_assets','cash','trading_assets','current_liabilities','noncurrent_liabilities','current_debt_investment']
        assert [r['key'] for r in report['rows']]==base+(['term_deposit'] if year<2024 else ['warranty'])
        values={}
        header=reader.pages[report['rows'][0]['page']-1].extract_text()
        compact=re.sub(r'\s+','',header)
        assert '合并资产负债表' in compact and '单位：元' in compact and f'{year}年12月31日' in compact
        labels=dict(zip(base,['流动资产合计','货币资金','交易性金融资产','流动负债合计','非流动负债合计','一年内到期的其他债权投资'])) | {'term_deposit':'定期存款','warranty':'产品质量保证'}
        for row in report['rows']:
            assert row['label']==labels[row['key']], 'concept differs'
            text=reader.pages[row['page']-1].extract_text()
            lines=[l.strip() for l in text.splitlines() if l.strip().startswith(row['label']+' ')]
            assert lines==[row['line']], 'PDF row differs'
            extracted=[v.replace(',','') for v in re.findall(r'-?[0-9,]+\.[0-9]{2}',lines[0])]
            assert extracted==row['amounts_cny'], 'PDF amount differs'
            expected_columns=(['current'] if (year,row['key'])==(2022,'current_debt_investment') else
                              ['comparative'] if (year,row['key'])==(2023,'term_deposit') else ['current','comparative'])
            assert row['columns']==expected_columns
            values[row['key']]={c:Decimal(v) for c,v in zip(row['columns'],extracted,strict=True)}
            field={'current_assets':'FN21','cash':'FN8','current_liabilities':'FN54'}.get(row['key'])
            assert row['tdx_field']==field
            if field:
                assert struct.unpack('<I',struct.pack('<f',float(values[row['key']]['current'])))[0]==record[0]['bits'][field], 'source bits differ'
                bits_checked+=1
            amounts_checked+=len(extracted)
        current=lambda k:values[k]['current']
        subtotal=current('current_assets')-current('cash')-current('current_liabilities')
        identified=current('trading_assets')+current('current_debt_investment')
        # 只加原文明确的本期金融分量；不把未列示定期存款认定为全部经济余额为零。
        if 'current' in values.get('term_deposit',{}):identified+=current('term_deposit')
        results.append(dict(year=year, amounts={k:{c:str(v) for c,v in cols.items()} for k,cols in values.items()},
            unclassified_current_subtotal_cny=str(subtotal),identified_financial_assets_cny=str(identified),
            subtotal_after_identified_asset_deduction_cny=str(subtotal-identified),
            full_operating_working_capital=None, full_reinvestment=None, historical_fcff=None))
    before,after=results[1:]
    delta=Decimal(after['amounts']['current_liabilities']['comparative'])-Decimal(before['amounts']['current_liabilities']['current'])
    noncurrent_delta=Decimal(after['amounts']['noncurrent_liabilities']['comparative'])-Decimal(before['amounts']['noncurrent_liabilities']['current'])
    warranty=Decimal(after['amounts']['warranty']['comparative'])
    assert delta==-warranty and noncurrent_delta==warranty
    return dict(results=results,pdf_amounts_checked=amounts_checked,source_bits_checked=bits_checked,
        comparison_2023=dict(current_liability_difference_cny=str(delta),noncurrent_liability_difference_cny=str(noncurrent_delta),
            numerical_bridge='equal_opposite_and_matches_warranty_comparative; not independent proof of reclassification cause'),
        decision='raw_current_balance_proxy_not_eligible_as_complete_operating_reinvestment_label')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf_directory',type=Path)
    args=parser.parse_args()
    print(json.dumps(verify(json.loads((DIRECTORY/'supor-capital-evidence.json').read_bytes()),args.pdf_directory),ensure_ascii=False,indent=2))
