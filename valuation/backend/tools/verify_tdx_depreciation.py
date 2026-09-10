"""中邮科技三期折旧列示核验：先闭合逐期范围，再聚合TTM，禁止重复加入分量。"""
import argparse
from decimal import Decimal,ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import struct

from pypdf import PdfReader
from tools.backtest_tdx_history import value


def verify(ledger,directory,source):
    if ledger['code']!='688648' or {r['period'] for r in ledger['reports']}!={'2025-06-30','2025-12-31','2026-06-30'} or len(ledger['reports'])!=3:
        raise ValueError('unsupported company/period scope')
    results=[]
    for report in ledger['reports']:
        path=Path(directory)/report['file']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=report['sha256']:raise ValueError('PDF hash differs')
        pdf=PdfReader(path);pages={}
        def text(page):
            if page not in pages:pages[page]=re.sub(r'\s+','',pdf.pages[page-1].extract_text())
            return pages[page]
        expected_title='中邮科技股份有限公司'+report['period'][:4]+('年年度报告' if report['period'].endswith('12-31') else '年半年度报告')
        if ledger['code'] not in text(1) or report['title']!=expected_title or expected_title not in text(1):raise ValueError('company/title differs')
        amounts={}
        for row in report['rows']:
            header=text(row['header_page']).replace(':','：')
            if row['header_label'] not in header or '单位：元币种：人民币' not in header:raise ValueError('section/unit differs')
            body=text(row['page'])
            if row['anchor']:
                if row['anchor'] not in body:raise ValueError('row anchor absent')
                body=body.split(row['anchor'],1)[1]
            matches=re.findall(re.escape(row['label'])+r'((?:-?[0-9,]+\.[0-9]{2})+)',body)
            if len(matches)!=1:raise ValueError('row not unique')
            values=[v.replace(',','') for v in re.findall(r'-?[0-9,]+\.[0-9]{2}',matches[0])]
            if values!=row['values']:raise ValueError('PDF row amounts differ')
            if row['key'] in amounts:raise ValueError('duplicate row key')
            amounts[row['key']]=Decimal(values[row['column']])
        labels={r['key']:r['label'] for r in report['rows']}
        included_rou='使用权资产折旧' in labels['cf_depreciation']
        expected_components=['ppe','investment_property']+(['right_of_use'] if included_rou else [])
        if report['fn136_components']!=expected_components:raise ValueError('FN136 component scope differs')
        note_sum=amounts['note_ppe']+amounts['note_property']+(amounts['note_rou'] if included_rou else 0)
        if amounts['cf_depreciation']!=note_sum:raise ValueError('depreciation note reconciliation differs')
        if not included_rou and amounts['cf_rou']!=amounts['note_rou']:raise ValueError('right of use note differs')
        candidates=[r for r in source['records'] if (r['code'],r['period'])==(ledger['code'],report['period'])]
        if len(candidates)!=1:raise ValueError('source identity not unique')
        record=candidates[0]
        fields={'FN136':('cf_depreciation',1),'FN137':('cf_intangible',1),'FN138':('cf_deferred',1)}
        if not included_rou:fields['FN581']=('cf_rou',10000)
        consumed=[];tdx_total=Decimal(0)
        for field,(key,multiplier) in fields.items():
            encoded=amounts[key]/multiplier
            if multiplier==10000:encoded=encoded.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            bits=struct.unpack('<I',struct.pack('<f',float(encoded)))[0]
            if record['bits'][field]!=bits:raise ValueError('TDX bits differ: '+field)
            amount=value(record,field)*multiplier;tdx_total+=amount
            consumed.append(dict(field=field,source_bits=bits,multiplier=multiplier,value_cny=str(amount),artifact=record['artifact']))
        combined={'FN579':'included_in_FN136'}
        if included_rou:combined['FN581']='included_in_FN136'
        for field in combined:
            if value(record,field)!=0:raise ValueError('expected combined-row source zero differs')
        pdf_total=amounts['cf_depreciation']+amounts['cf_intangible']+amounts['cf_deferred']+(0 if included_rou else amounts['cf_rou'])
        results.append(dict(period=report['period'],fn136_components=expected_components,source_inputs=consumed,
                            unconsumed_source_fields=combined,reported_da_cny=str(tdx_total),pdf_reported_da_cny=str(pdf_total),source_minus_pdf_cny=str(tdx_total-pdf_total)))
    by_period={r['period']:r for r in results};coefficients={'2025-12-31':1,'2026-06-30':1,'2025-06-30':-1}
    tdx_ttm=sum((Decimal(by_period[p]['reported_da_cny'])*c for p,c in coefficients.items()),Decimal(0))
    pdf_ttm=sum((Decimal(by_period[p]['pdf_reported_da_cny'])*c for p,c in coefficients.items()),Decimal(0))
    return dict(code=ledger['code'],period='2026-06-30',status='reported_depreciation_scope_reconciled',periods=results,coefficients=coefficients,
                reported_da_ttm_cny=str(tdx_ttm),pdf_reported_da_ttm_cny=str(pdf_ttm),source_minus_pdf_cny=str(tdx_ttm-pdf_ttm),
                actual_fcff=None,boundary='仅三期合并报表D&A列示；TDX数值逐期按已核对范围聚合，未恢复源精度；未闭合经营分类/税费/非现金再投资，不新增标准事实或估值结论')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('ledger',type=Path);parser.add_argument('snapshot',type=Path);args=parser.parse_args()
    try:
        raw=args.snapshot.read_bytes();ledger_raw=args.ledger.read_bytes();ledger=json.loads(ledger_raw)
        if hashlib.sha256(raw).hexdigest()!=ledger['snapshot_sha256']:raise ValueError('snapshot hash differs')
        result=verify(ledger,args.ledger.parent,json.loads(raw))
        result['evidence']=dict(ledger_sha256=hashlib.sha256(ledger_raw).hexdigest(),snapshot_sha256=ledger['snapshot_sha256'],code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,KeyError,IndexError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
