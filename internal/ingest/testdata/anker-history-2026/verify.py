"""安克六年历史专用离线验收；保留比较列版本，不还原历史当时已知数据。"""
import argparse
import csv
import hashlib
import io
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
AS_OF = '2026-09-06T00:00:00+00:00'


def main(write=False):
    reports = json.loads((ROOT/'reports.json').read_text())
    blobs = {}
    for id, meta in reports.items():
        raw = (ROOT/meta['file']).read_bytes()
        assert len(raw) == meta['size'] and hashlib.sha256(raw).hexdigest() == meta['sha256'], id
        blobs[id] = raw
    texts = {}
    for id, meta in reports.items():
        if 'years' not in meta:
            continue
        assert len(meta['years']) == 2 and meta['years'][1] == meta['years'][0]-1
        entries = json.loads(blobs[meta['catalogue']])['announcements']
        a, = [a for a in entries if a['announcementId'] == id]
        assert a['secCode'] == '300866' and a['announcementTitle'] == f"{meta['years'][0]}年年度报告"
        assert meta['url'] == 'https://static.cninfo.com.cn/'+a['adjunctUrl']
        china = timezone(timedelta(hours=8))
        day = datetime.fromtimestamp(a['announcementTime']/1000,china).date()
        assert datetime.combine(day+timedelta(days=1),datetime.min.time(),china) <= datetime.fromisoformat(AS_OF)
        texts[id] = '\n'.join(f'=== 页 {i+1} ===\n'+p.extract_text() for i,p in enumerate(PdfReader(io.BytesIO(blobs[id])).pages))
    rows = list(csv.DictReader((ROOT/'reported.csv').open()))
    assert len(rows) == len({r['id'] for r in rows}) == 180
    values = {}
    for r in rows:
        text = texts[r['pdf_id']]
        start = text.index(r['section'])
        section = text[start:text.index(r['end_section'],start+len(r['section']))]
        match, = list(re.finditer('^'+re.escape(r['label'])+r'[^\n]*',section,re.M))
        token = re.findall(r'(?<!\S)(?:-?[\d,]+\.\d{2}|-)(?!\S)',match[0])[int(r['column'])]
        page = re.findall(r'=== 页 (\d+) ===',text[:start+match.start()])[-1]
        amount = D('0' if token == '-' else token.replace(',',''))
        assert page == r['pdf_page'] and token == r['printed'] and amount == D(r['value']),r
        assert int(r['year']) == reports[r['pdf_id']]['years'][int(r['column'])]
        assert r['id'] == r['year']+'/'+r['key']
        values[int(r['year']),r['key']] = amount
    output = []
    for year in range(2020,2026):
        v = {k:amount for (y,k),amount in values.items() if y==year}
        assert len(v) == 30
        assert sum((amount for k,amount in v.items() if k.startswith('cf_') and k!='cf_ocf'),D(0)) == v['cf_ocf'] == v['ocf']
        da = sum((v['cf_'+k+'_da'] for k in ['property','fixed','rou','intangible','deferred']),D(0))
        trade = v['ar']+v['inventory']+v['prepayments']-v['ap']-v['contract_liabilities']
        amount_fields = {k:v[k] for k in ['revenue','rd_expense','cash_capex','disposal_cash','ocf','ar','inventory','prepayments','ap','contract_liabilities']}
        amount_fields.update(reported_da=da,rou_depreciation=v['cf_rou_da'],net_cash_capex=v['cash_capex']-v['disposal_cash'],trade_wc_subset=trade,cf_wc_cash_use=-(v['cf_inventory']+v['cf_receivables']+v['cf_payables']))
        source, = {r['pdf_id'] for r in rows if int(r['year'])==year}
        output.append(dict(year=year,**{k:f'{amount:.2f}' for k,amount in amount_fields.items()},unit='CNY',source_pdf=source,
            basis='2023 report restated comparison; stock column is 2023-01-01' if year==2022 else 'comparison column' if year%2==0 else 'current year column',
            lease_basis='pre-2021 lease adoption; no comparative restatement' if year==2020 else 'post-2021 lease adoption',
            status='historical_inputs_trade_wc_subset_not_full_operating_wc',as_of=AS_OF))
    # 与现有样本中相同年度/余额项目交叉核对；研发没有猜测 TDX 映射。
    existing = {r['id']:D(r['value']) for r in csv.DictReader((ROOT/'../anker-valuation-2026/reported.csv').open())}
    for key,old in [('revenue','revenue'),('cash_capex','cf_capex'),('disposal_cash','cash_disposals'),('ocf','cf_ocf_statement'),('ar','receivables'),('inventory','inventory'),('prepayments','prepayments'),('ap','payables'),('contract_liabilities','contract_liabilities')]:
        assert values[2025,key] == existing['2025-12-31/'+old],key
    # 五年年末资本化、次年起直线摊销；分析资产不是报表确认资产。
    cohorts = []
    opening = amortization = closing = D(0)
    for year in range(2020,2026):
        expense = values[year,'rd_expense']
        before = expense*D(max(0,5-(2024-year)))/5 if year<2025 else D(0)
        charge = expense/5 if year<2025 else D(0)
        addition = expense if year==2025 else D(0)
        after = expense*D(max(0,5-(2025-year)))/5
        assert before+addition-charge == after
        opening += before
        amortization += charge
        closing += after
        cohorts.append(dict(vintage=year,analysis_year=2025,life_years=5,rd_expense=f'{expense:.6f}',opening_asset=f'{before:.6f}',addition=f'{addition:.6f}',amortization=f'{charge:.6f}',closing_asset=f'{after:.6f}',unit='CNY',source=f'{year}/rd_expense',status='illustrative_model_asset_not_reported',as_of=AS_OF))
    delta = values[2025,'rd_expense']-amortization
    assert closing-opening == delta
    base_rows = [r for r in csv.DictReader((ROOT/'../anker-valuation-2026/valuation-scenarios.csv').open()) if r['period']=='2025-12-31']
    assert len(base_rows)==3 and {r['assumed_tax_rate'] for r in base_rows}=={'0.15','0.20','0.25'}
    scenarios = []
    for r in base_rows:
        assert r['as_of']==AS_OF and r['unit']=='CNY' and r['fcff']==''
        base_ebit, tax, investment = (D(r[k]) for k in ['adjusted_ebit','modelled_operating_tax','identified_net_longlived_investment'])
        assert base_ebit-tax == D(r['value'])
        nopat = base_ebit+delta-tax
        adjusted_investment = investment+delta
        subtotal = nopat-adjusted_investment
        assert subtotal == D(r['subtotal_before_wc_and_other_adjustments'])
        scenarios.append(dict(year=2025,life_years=5,assumed_tax_rate=r['assumed_tax_rate'],opening_research_asset=f'{opening:.6f}',closing_research_asset=f'{closing:.6f}',research_amortization=f'{amortization:.6f}',ebit_and_reinvestment_adjustment=f'{delta:.6f}',adjusted_ebit=f'{base_ebit+delta:.6f}',unchanged_modelled_tax=f'{tax:.6f}',adjusted_nopat=f'{nopat:.6f}',adjusted_identified_net_investment=f'{adjusted_investment:.6f}',subtotal_before_wc_and_other_adjustments=f'{subtotal:.6f}',fcff='',unit='CNY',status='illustrative_reclassification_tax_unchanged_not_fcff',policy='five-year straight-line from following year; base model tax unchanged; no additional R&D tax benefit or OCI adjustment',as_of=AS_OF))
    for name,data in [('annual-inputs',output),('rd-cohorts',cohorts),('rd-capitalization',scenarios)]:
        out = io.StringIO()
        writer = csv.DictWriter(out,fieldnames=data[0],lineterminator='\n')
        writer.writeheader()
        writer.writerows(data)
        path = ROOT/(name+'.csv')
        if write:
            path.write_text(out.getvalue())
        else:
            assert path.read_text()==out.getvalue(),name+'.csv differs; review before --write'
    print('通过：三份年报、180 个原文金额、六年历史及五年研发资本化/税项不变情景。')



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true',help='验收后重建历史与研发情景表；默认只核对')
    main(parser.parse_args().write)
