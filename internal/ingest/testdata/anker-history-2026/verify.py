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
    out = io.StringIO()
    writer = csv.DictWriter(out,fieldnames=output[0],lineterminator='\n')
    writer.writeheader()
    writer.writerows(output)
    path = ROOT/'annual-inputs.csv'
    if write:
        path.write_text(out.getvalue())
    else:
        assert path.read_text() == out.getvalue(), 'annual-inputs.csv differs; review before --write'
    print('通过：三份年报、180 个原文金额、六期 OCF 十九项调节及 2020—2025 六年输入表。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true',help='验收后重建历史表；默认只核对')
    main(parser.parse_args().write)
