"""贵州茅台专用：离线核对财报、金融口径及验证性估值，不写生产事实。"""
import argparse
import csv
import hashlib
import io
import json
import re
import sys
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
from pathlib import Path
from pypdf import PdfReader

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent
AS_OF='2026-09-06T00:00:00+00:00'
PERIODS=['FY2025','H12025','H12026']


def csv_text(rows):
    out=io.StringIO();w=csv.DictWriter(out,fieldnames=rows[0],lineterminator='\n');w.writeheader();w.writerows(rows)
    return out.getvalue()


def check_outputs(outputs,write=False):
    for name,rows in outputs.items():
        for r in rows:
            for k,v in list(r.items()):
                if isinstance(v,D):r[k]=format(v,'f')
            r['information_as_of']=AS_OF
        expected=csv_text(rows);p=ROOT/(name+'.csv')
        if write:p.write_text(expected)
        else:assert p.read_text()==expected,name+' differs; review evidence/policy before --write'


def financial(texts,write=False):
    entries=json.loads((ROOT/'evidence.json').read_text());assert len(entries)==279
    values={};ledger=[]
    for e in entries:
        t=texts[e['pdf_id']];start=t.index(e['section']);stop=t.index(e['end_section'],start+len(e['section']));sec=t[start:stop]
        assert sec.count(e['quote'])==1,e
        pos=start+sec.index(e['quote'])
        assert int(re.findall(r'=== 页 (\d+) ===',t[:pos])[-1])==e['pdf_page'],e
        tokens=re.findall(r'(?<![\d,])-?[\d,]+\.\d{2}(?!\d)',e['quote'])
        for c in e['cells']:
            assert c['id'] not in values and c['multiplier'] in ['1','10000','100000000'],c
            key=c['id'].split('/')[1]
            expected_unit='100000000' if key in ['finance_assets','finance_liabilities','finance_equity','finance_revenue','finance_pbt','finance_net_income'] else '10000' if key in ['moutai_revenue','series_revenue'] else '1'
            assert c['multiplier']==expected_unit,('source unit',c)
            assert tokens[c['token']]==c['printed'],c
            amount=D(c['printed'].replace(',',''))*D(c['multiplier']);assert amount==D(c['value']),c
            values[c['id']]=amount
            ledger.append(dict(id=c['id'],value=amount,pdf_id=e['pdf_id'],pdf_page=e['pdf_page'],printed=c['printed'],multiplier=c['multiplier'],status='reported_value_at_source_precision'))
    assert len(values)==437
    v=lambda p,k:values[p+'/'+k]
    def total(p,keys):return sum((v(p,k) for k in keys.split()),D(0))
    comparison=[k for k in values if k.startswith('H12025-comparison/')]
    assert len(comparison)==61
    for k in comparison:assert values[k]==values[k.replace('H12025-comparison/','H12025/')],k
    bridges=[]
    for p in PERIODS:
        assert v(p,'revenue_total')==total(p,'revenue financial_interest_income')
        assert v(p,'cost_total')==total(p,'cogs financial_interest_expense financial_fees tax_surcharges selling admin rd finance_cost')
        assert v(p,'operating_profit')==v(p,'revenue_total')-v(p,'cost_total')+total(p,'other_income investment_income fair_value_income credit_impairment disposal_income')
        assert v(p,'pbt')==total(p,'operating_profit nonoperating_income')-v(p,'nonoperating_expense')
        assert v(p,'net_income')==v(p,'pbt')-v(p,'income_tax')==total(p,'parent_income minority_income')
        assert v(p,'operating_in')==total(p,'sales_cash deposit_change financial_cash_in other_operating_in')
        assert v(p,'operating_out')==total(p,'purchases_cash loan_change interbank_change lending_change financial_cash_out payroll_cash tax_cash other_operating_out')
        assert v(p,'ocf')==v(p,'operating_in')-v(p,'operating_out')
        components={k:a for k,a in values.items() if k.startswith(p+'/cf_')}
        assert len(components)==16 and sum(components.values(),D(0))==v(p,'ocf')
        fin_cash=total(p,'deposit_change financial_cash_in')-total(p,'loan_change interbank_change lending_change financial_cash_out')
        residual_cash=v(p,'ocf')-fin_cash
        assert residual_cash==total(p,'sales_cash other_operating_in')-total(p,'purchases_cash payroll_cash tax_cash other_operating_out')
        net_fin=v(p,'financial_interest_income')-total(p,'financial_interest_expense financial_fees')
        # 金融净利息及全部投资/公允价值/信用分量剔除；未分配金融管理费仍在代理利润中。
        ebit=v(p,'operating_profit')-net_fin-total(p,'investment_income fair_value_income credit_impairment disposal_income')+v(p,'borrowing_interest')-v(p,'treasury_interest')
        core=v(p,'revenue')-total(p,'cogs tax_surcharges selling admin rd')-(v(p,'finance_cost')-v(p,'borrowing_interest')+v(p,'treasury_interest'))+v(p,'other_income')
        assert ebit==core
        assert abs(v(p,'finance_assets')-v(p,'finance_liabilities')-v(p,'finance_equity'))<=D('1500000'),p # 三项亿元小数各有舍入。
        bridges.append(dict(period=p,revenue=v(p,'revenue'),consolidated_ocf=v(p,'ocf'),identified_financial_cash_effect=fin_cash,ocf_after_identified_financial_lines=residual_cash,
            financial_net_interest=net_fin,treasury_interest=v(p,'treasury_interest'),liquor_ebit_proxy=ebit,finance_standalone_pbt=v(p,'finance_pbt'),
            financial_net_interest_less_standalone_pbt=net_fin-v(p,'finance_pbt'),consumption_tax=v(p,'consumption_tax'),income_tax_expense=v(p,'income_tax'),
            capex=v(p,'capex'),da=total(p,'cf_fixed_da cf_rou_da cf_intangible_da cf_deferred_da'),historical_liquor_fcff='',pure_liquor_ocf='',status='financial_lines_removed_unallocated_costs_and_eliminations_remain'))
    stocks=[]
    for p in ['2025-12-31','2026-06-30']:
        assert v(p,'assets')==total(p,'liabilities equity') and v(p,'equity')==total(p,'parent_equity minority_equity')
        financial_gross=total(p,'cash interbank_assets reverse_repo current_financial_maturity loans debt_investments other_debt_investments')
        wc=total(p,'ar notes_receivable prepayments other_receivable inventory other_current_assets')-total(p,'ap contract_liabilities payroll_payable other_payable other_current_liabilities tax_payable')+v(p,'income_tax_payable')
        stocks.append(dict(period=p,financial_gross=financial_gross,external_deposits=v(p,'deposits_liability'),financial_net_before_other_claims=financial_gross-v(p,'deposits_liability'),
            risk_investments=total(p,'funds associates'),lease_debt=total(p,'leases current_leases'),inventory=v(p,'inventory'),wc_proxy=wc,other_wc_proxy=wc-v(p,'inventory'),
            shares=v(p,'shares'),status='consolidated_financial_pool_not_distributable_cash_or_complete_segment_split'))
    assert total('2026-06-30','shares_open shares_cancelled')==v('2026-06-30','shares_close')==v('2026-06-30','shares')
    assert total('2026-06-30','treasury_open treasury_added')==v('2026-06-30','treasury_cancelled') # 期末空白以变动证明归零，不由缺数推定。
    risk=texts['1225475863'];ownership=risk[risk.index('财务公司股权结构'):risk.index('二、财务公司内控情况')]
    assert '贵州茅台酒股份有限公司 12.75 51' in ownership and '10 40' in ownership and '2.25 9' in ownership
    assert '至少需要五年' in texts['1225114741']
    balance=texts['1225475868'].split('合并资产负债表',1)[1].split('母公司资产负债表',1)[0]
    for label in ['短期借款','长期借款','应付债券']:
        line,=re.findall('^'+label+r'[^\n]*',balance,re.M)
        assert line==label,('unexpected borrowing amount',line)
    # 受限资产账面值与现金流口径不同，保留差额，不凑成一致。
    restricted_difference=v('2026-06-30','restricted_cash_book')-v('2026-06-30','restricted_cash_cf')
    assert restricted_difference==D('3492871.40')
    history=[]
    for year in range(2020,2026):
        p='FY'+str(year)
        history.append(dict(year=year,**{k:v(p,k) for k in ['revenue','operating_profit','net_income','ocf','capex','inventory']},status='consolidated_history_not_pure_liquor'))
    check_outputs({'reported':ledger,'financial-bridge':bridges,'balance-bridge':stocks,'annual-history':history},write)
    return values,bridges,stocks


def main(write=False):
    reports=json.loads((ROOT/'reports.json').read_text());blobs={};texts={}
    for id,m in reports.items():
        raw=(ROOT/m['file']).read_bytes();assert len(raw)==m['size'] and hashlib.sha256(raw).hexdigest()==m['sha256'],id
        blobs[id]=raw
    for id,m in reports.items():
        if not m['file'].endswith('.pdf'):continue
        entries=json.loads(blobs[m['catalogue']])['announcements'];a,=[a for a in entries if a['announcementId']==id]
        assert a['secCode']=='600519' and a['announcementTitle']==m['title'] and m['url']=='https://static.cninfo.com.cn/'+a['adjunctUrl']
        china=timezone(timedelta(hours=8));day=datetime.fromtimestamp(a['announcementTime']/1000,china).date()
        assert datetime.combine(day+timedelta(days=1),datetime.min.time(),china)<=datetime.fromisoformat(AS_OF)
        reader=PdfReader(io.BytesIO(blobs[id]));texts[id]='\n'.join(f'=== 页 {i+1} ===\n'+p.extract_text() for i,p in enumerate(reader.pages))
    values,bridges,stocks=financial(texts,write)
    from valuation import verify_model
    check_outputs(verify_model(values,bridges,stocks),write)
    print('通过：8 份 PDF、279 段原文/437 个数值、六年历史、金融口径及茅台验证性估值。')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--write',action='store_true',help='先对账再重建样本表；默认离线只读');main(p.parse_args().write)
