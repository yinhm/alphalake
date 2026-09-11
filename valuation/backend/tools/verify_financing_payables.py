"""到期应付款定向分类；不把关联方借款说明差额归零。"""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
from tools import verify_cnty_debt as cnty, verify_debt_maturities as maturity
from tools.verify_five_debt import MONEY

DIRECTORY=maturity.DIRECTORY


def verify():
    ledger,reports,records,artifacts=maturity.load_inputs()
    base=maturity.verify(ledger,reports,records,artifacts)
    amounts={(r['code'],r['period']):r for r in base['results']}
    def current_payable(code,year):
        return Decimal(next(c['amount_cny'] for c in amounts[code,f'{year}-12-31']['components'] if c['label']=='一年内到期的长期应付款'))
    def page(code,year,n):
        path,sha=reports[code,year]
        if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('PDF hash differs')
        return subprocess.check_output(['pdftotext','-f',str(n),'-l',str(n),'-layout',str(path),'-'],text=True)
    def values(body):return [Decimal(x.replace(',','')) for x in MONEY.findall(body)]
    def check(actual,expected):
        if actual!=list(map(Decimal,expected)):raise ValueError('financing amounts differ')
    results=[]
    # Existing main-table/source check also verifies both financing roll-forwards.
    cledger,csource=cnty.load_inputs();cnty.verify(cledger,csource)
    rows={r['key']:list(map(Decimal,r['values'])) for r in cledger['rows']}
    for year,column in ((2021,1),(2022,0)):
        current=current_payable('000035',year)
        if rows['financing_payables'][column]-rows['payables'][column]!=current:raise ValueError('financing roll-forward differs')
        results.append(dict(code='000035',period=f'{year}-12-31',current_payable_cny=str(current),
            status='financing_nature_supported_by_gross_less_noncurrent',unclassified_current_payable_cny='0.00',
            principal_interest_split=None,basis='2022_report_note_with_2021_comparative; existing_original_source_checks'))
    text=page('000546',2021,212)
    body=text.split('（3）一年内到期的长期应付款',1)[1].split('30、其他流动负债',1)[0]
    vals=[]
    for label in ('售后回租款','分期购车款'):
        lines=[l for l in body.splitlines() if re.sub(r'\s+','',l).startswith(label)]
        if len(lines)!=1:raise ValueError('financing row not unique')
        vals+=values(lines[0])
    check(vals,['7681904.45','11444532.83'])
    if sum(vals)!=current_payable('000546',2021):raise ValueError('installment breakdown differs')
    results.append(dict(code='000546',period='2021-12-31',status='sale_leaseback_and_installment_purchase_identified',
        current_payable_cny=str(sum(vals)),sale_leaseback_cny=str(vals[0]),installment_vehicle_purchase_cny=str(vals[1]),
        unclassified_current_payable_cny='0.00',principal_interest_split=None))
    for year,n,expected in [(2021,171,['60094359.47','12094684.24','9447868.23','16152406.30','69542227.70']),
                            (2022,153,['49383701.17','60094359.47','23641262.81','9447868.23','75615153.23'])]:
        text=page('000599',year,n)
        current=text.split('一年内到期的非流动负债',1)[1].split('长期应付款',1)[0]
        noncurrent=text.split('长期应付款',1)[1].split('7、其他',1)[0]
        for block in (current,noncurrent):
            if '青岛金之桥投资管理有限公司' not in re.sub(r'\s+','',MONEY.sub('',block)):
                raise ValueError('related-party identity differs')
        narrative=re.sub(r'\s+','',text.split('7、其他',1)[1])
        matches=re.findall(r'借款余额为([0-9,]+\.[0-9]{2})元',narrative)
        if len(matches)!=1:raise ValueError('borrowing narrative differs')
        vals=values(current)+values(noncurrent)+[Decimal(matches[0].replace(',',''))]
        check(vals,expected)
        gap=vals[-1]-vals[0]-vals[2]
        residual=current_payable('000599',year)-vals[0]
        if residual<0:raise ValueError('related-party amount exceeds total')
        results.append(dict(code='000599',period=f'{year}-12-31',status='partial_related_party_identification',
            current_payable_cny=str(current_payable('000599',year)),related_current_cny=str(vals[0]),
            related_noncurrent_cny=str(vals[2]),narrative_borrowing_cny=str(vals[-1]),
            narrative_less_current_and_noncurrent_cny=str(gap),unclassified_current_payable_cny=str(residual),
            principal_interest_split=None))
    covered={(r['code'],r['period']) for r in results}
    for r in base['results']:
        for c in r['components']:
            if c['classification']=='payable_nature_unresolved' and (r['code'],r['period']) not in covered:
                results.append(dict(code=r['code'],period=r['period'],status='current_nature_not_proven_by_noncurrent_note',
                    current_payable_cny=c['amount_cny'],unclassified_current_payable_cny=c['amount_cny'],principal_interest_split=None))
    if len(results)!=8:raise ValueError('payable denominator differs')
    return dict(results=results,full_debt=None,economic_roic=None,
        boundary='eight_nonzero_current_payables_preserved; nature_identification_not_principal_estimate; '
        'narrative_gap_and_unclassified_residual_not_zero; no_standard_or_forecast_update')


if __name__=='__main__':
    print(json.dumps(verify(),ensure_ascii=False,indent=2))
