"""两家公司：生产标准查询优先，PDF 独立核验及显式补充，原 PDF 模型保留作对照。"""
import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import struct
import subprocess
import sys
from decimal import Decimal as D, localcontext, ROUND_HALF_UP

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[3]
ASOF='2026-09-06T00:00:00+00:00'
PERIODS=['2025-06-30','2025-12-31','2026-06-30']


def read(path):return list(csv.DictReader(path.open()))


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def csv_text(rows):
    out=io.StringIO();w=csv.DictWriter(out,fieldnames=rows[0],lineterminator='\n');w.writeheader();w.writerows(rows);return out.getvalue()


def build():
    facts={(r['code'],r['period'],r['field']):r for r in read(ROOT/'facts.csv')}
    windows={(r['code'],r['field']):r for r in read(ROOT/'windows.csv')}
    supplied={(r['code'],r['period'],r['item']):r for r in read(ROOT.parent/'supplement-review-2026/resolved.csv') if r['route']!='tdx_standard'}
    used_supplements=set()
    audit={};inputs_out=[];forecast_out=[];bridge_out=[];results_out=[];sensitivity_out=[]
    packages={r['sample_sha256']:r for r in json.loads((ROOT/'packages.json').read_text())}
    for sha,p in packages.items():assert hashlib.sha256((ROOT/p['file']).read_bytes()).hexdigest()==sha
    for r in facts.values():
        assert r['artifact_sha256'] in packages and r['statement_scope']=='provider_default'
        raw=struct.unpack('<f',struct.pack('<I',int(r['bits'])))[0]
        assert abs(D(r['value'])-D.from_float(raw)*D(r['multiplier']))<D('.0000001')
    def source(code,p,key,pdf,field,locator):
        if not field:
            identity=(code,p,key);r=supplied.get(identity)
            assert r is not None,('undeclared supplement; no implicit PDF fallback',identity)
            assert r['pdf_locator']==locator and D(r['value'])==pdf and r['information_as_of']==ASOF
            assert r['unit']==('CNY/share' if key=='extra_conversion_price' else 'CNY')
            value=D(r['value']);status='verified_cninfo_supplement';refs=r['pdf_sha256']+'/'+r['announcement_id']
            used_supplements.add(identity)
        else:
            r=facts.get((code,p,field));assert r is not None,('required standard fact missing; no PDF fallback',code,p,key,field)
            multiplier=D(r['multiplier'])
            encoded=(pdf/multiplier).quantize(D('.01'),rounding=ROUND_HALF_UP)
            expected=struct.unpack('<I',struct.pack('<f',float(encoded)))[0]
            assert expected==int(r['bits']),('PDF/source conflict',code,p,key,field,pdf,r['value'])
            value=D(r['value']);status='standard_fact_pdf_bits_verified';refs=r['artifact_sha256']+'/'+r['announcement_id']
        audit[(code,p,key)]=dict(code=code,period=p,item=key,field=field or '',selected_value=str(value),pdf_value=str(pdf),difference=str(value-pdf),status=status,source=refs,pdf_locator=locator)
        return value
    def revenue(code,p,pdf,locator):
        months=['03-31','06-30','09-30','12-31'][:2 if p.endswith('06-30') else 4]
        rs=[facts[(code,p[:4]+'-'+m,'FN230')] for m in months]
        value=sum((D(r['value']) for r in rs),D(0));bound=D(0)
        for r in rs:
            bits=int(r['bits']);v=struct.unpack('<f',struct.pack('<I',bits))[0]
            step=max(abs(struct.unpack('<f',struct.pack('<I',bits+d))[0]-v) for d in [-1,1]);bound+=D.from_float(step)/2
        assert abs(value-pdf)<=bound+D('.01'),('quarter revenue/PDF mismatch',code,p,value,pdf,bound)
        audit[(code,p,'revenue')]=dict(code=code,period=p,item='revenue',field='FN230',selected_value=str(value),pdf_value=str(pdf),difference=str(value-pdf),status='standard_quarter_sum_pdf_ulp_verified',source=';'.join(r['artifact_sha256']+'/'+r['announcement_id'] for r in rs),pdf_locator=locator)
        return value
    def window(code,field,expected):
        r=windows[(code,field)];assert r['coverage_status']=='complete' and r['available_inputs']==r['required_inputs'],('incomplete standard window',code,field)
        assert abs(D(r['value'])-expected)<D('.0000001'),('window vs components',code,field)
        return D(r['value'])
    for code,company in [('600519','moutai'),('300866','anker')]:
        sample=ROOT.parent/(company+'-valuation-2026')
        printed=read(sample/'reported.csv')
        if company=='moutai':
            aliases={'FY2025':'2025-12-31','H12025':'2025-06-30','H12026':'2026-06-30'}
            pdf={aliases.get(r['id'].split('/')[0],r['id'].split('/')[0])+'/'+r['id'].split('/')[1]:r for r in printed if not r['id'].startswith('H12025-comparison/')}
            mapping=dict(cash='FN8',ar='FN11',inventory='FN17',associates='FN25',assets='FN40',ap='FN44',current_leases='FN52',liabilities='FN63',minority_equity='FN69',equity='FN72',finance_cost='FN80',operating_profit='FN86',pbt='FN92',income_tax='FN93',capex='FN114',cash_equivalents='FN133',cf_fixed_da='FN136',cf_intangible_da='FN137',cf_deferred_da='FN138',shares='FN238',parent_equity='FN271',borrowing_interest='FN305',treasury_interest='FN306',leases='FN439',cf_rou_da='FN581')
            model=module('moutai_model',sample/'valuation.py').model
            baseline={r['item']:D(r['value']) for r in read(sample/'model-inputs.csv')}
        else:
            pdf={r['id']:r for r in printed}
            mapping={r['key']:r['provider_field'] for r in printed if r['provider_field']}
            mapping.update(cf_fixed_depreciation='FN136',cf_rou_depreciation='FN581',cf_intangible_amortization='FN137',cf_deferred_amortization='FN138')
            extra=json.loads((ROOT.parent/'anker-dcf-2026/evidence.json').read_text())
            for e in extra:
                for c in e['cells']:pdf['2026-06-30/extra_'+c['key']]=dict(value=c['value'],pdf_id=e['pdf_id'],pdf_page=e['pdf_page'])
            mapping.update(extra_cash_equivalents='FN133',extra_associates='FN25',extra_shares_close='FN238')
            model=module('anker_model',ROOT.parent/'anker-dcf-2026/verify.py').model
            baseline={r['item']:D(r['value']) for r in read(ROOT.parent/'anker-dcf-2026/inputs.csv')}
        mapping.update(net_income='FN95', other_current_assets='FN20', other_current_liabilities='FN53')
        mapping.update(investment_income='FN83', fair_value_income='FN82', disposal_income='FN301')
        if company == 'moutai':
            mapping.update(current_financial_maturity='FN19', other_payable='FN50', minority_income='FN97')
            mapping.update(prepayments='FN12', other_receivable='FN13', payroll_payable='FN46', tax_payable='FN47')
        mapping.update(interbank_assets='FN403', reverse_repo='FN409', loans='FN411', deposits_liability='FN413', debt_investments='FN430', other_debt_investments='FN431', funds='FN433', contract_liabilities='FN434', notes_receivable='FN437', financial_interest_income='FN506', financial_interest_expense='FN509', financial_fees='FN510', credit_impairment='FN520') if company == 'moutai' else mapping.update(trading_assets='FN9', extra_provisions='FN59', extra_convertible_equity_book='FN299', cf_property_depreciation='FN579')
        def v(p,k):
            row=pdf[p+'/'+k];amount=D(row['value']);locator=company+'/'+row['pdf_id']+'.pdf#page='+str(row['pdf_page'])
            return revenue(code,p,amount,locator) if k=='revenue' else source(code,p,k,amount,mapping.get(k),locator)
        def total(p,keys):return sum((v(p,k) for k in keys.split()),D(0))
        def ttm(k):
            expected=v(PERIODS[1],k)+v(PERIODS[2],k)-v(PERIODS[0],k)
            field='FN230' if k=='revenue' else mapping.get(k)
            return window(code,field,expected) if field else expected
        end='2026-06-30'
        if company=='moutai':
            ebit=ttm('operating_profit')-ttm('financial_interest_income')+ttm('financial_interest_expense')+ttm('financial_fees')-sum((ttm(k) for k in ['investment_income','fair_value_income','credit_impairment','disposal_income']),D(0))+ttm('borrowing_interest')-ttm('treasury_interest')
            wc=total(end,'ar notes_receivable prepayments other_receivable inventory other_current_assets')-total(end,'ap contract_liabilities payroll_payable other_payable other_current_liabilities tax_payable')+v(end,'income_tax_payable')
            i=dict(revenue_ttm=ttm('revenue'),ebit_ttm=ebit,da_ttm=sum((ttm(k) for k in ['cf_fixed_da','cf_rou_da','cf_intangible_da','cf_deferred_da']),D(0)),inventory=v(end,'inventory'),other_wc=wc-v(end,'inventory'),
                financial_gross=total(end,'cash interbank_assets reverse_repo current_financial_maturity loans debt_investments other_debt_investments'),risk_assets=total(end,'funds associates'),external_deposits=v(end,'deposits_liability'),finance_book_equity=v(end,'finance_equity'),income_tax_payable=v(end,'income_tax_payable'),lease_debt=total(end,'current_leases leases'),shares=v(end,'shares'),operating_minority_fraction_proxy=(ttm('minority_income')-ttm('finance_net_income')*D('.49'))/(ebit*D('.75')))
            assumptions=json.loads((sample/'assumptions.json').read_text())
        else:
            # 相同公司政策的分量公式；每个取值均通过标准事实/显式补充路由。
            ebit=sum((ttm(k)*c for k,c in [('operating_profit',1),('interest_expense',1),('interest_income',-1),('investment_income',-1),('forward_realized',1),('fair_value_income',-1),('fv_forward_asset',1),('fv_forward_liability',1),('disposal_income',-1)]),D(0))
            wc=sum((v(end,k)*c for k,c in [('current_assets',1),('cash',-1),('trading_assets',-1),('trading_forward_asset',1),('deposits',-1),('current_liabilities',-1),('short_debt',1),('current_debt',1),('loan_receivable',-1),('loan_allowance',1),('dividends_receivable',-1),('income_tax_payable',1),('ipo_prepaid',-1),('repurchase_payable',1),('capex_payable',1),('ipo_payable',1)]),D(0))
            x=lambda k:v(end,'extra_'+k)
            i=dict(revenue_ttm=ttm('revenue'),ebit_ttm=ebit,opening_wc=wc,da_ttm=sum((ttm(k) for k in ['cf_property_depreciation','cf_fixed_depreciation','cf_rou_depreciation','cf_intangible_amortization','cf_deferred_amortization']),D(0)),cash_equivalents=x('cash_equivalents'),other_cash_candidate=v(end,'cash')-x('cash_equivalents')-x('restricted_cash'),financial_debt_assets=x('current_financial_debt')+x('noncurrent_financial_debt')+v(end,'deposits'),risky_investments=x('current_financial_equity')+x('noncurrent_financial_equity')+x('associates')+v(end,'loan_receivable')-v(end,'loan_allowance'),minority=v(end,'minority'),debt=total(end,'short_debt long_debt current_loans bonds current_bonds leases current_leases'),other_claims=total(end,'income_tax_payable repurchase_payable capex_payable ipo_payable')+x('provisions'),shares=x('shares_close'),convertible_debt=total(end,'bonds current_bonds'),convertible_equity_book=x('convertible_equity_book'),convertible_face=D(''.join(re.search(r'(1,104,660,8)\n(00\.00)',next(e['quote'] for e in extra if e['quote'].startswith('11,048,200 '))).groups()).replace(',','')),conversion_price=x('conversion_price'))
            # 转债面值是已锁定跨行披露，不是可用 TDX 映射；显式记录补充输入。
            source(code,end,'convertible_face',i['convertible_face'],None,'anker/1225533054.pdf#page=54')
            assumptions=json.loads((ROOT.parent/'anker-dcf-2026/assumptions.json').read_text())
        assert set(i)==set(baseline)
        for k,value in i.items():inputs_out.append(dict(code=code,item=k,value=value,pdf_model_value=baseline[k],difference=value-baseline[k]))
        old=read(sample/'valuation.csv' if company=='moutai' else ROOT.parent/'anker-dcf-2026/valuation.csv')
        for scenario,previous in zip(assumptions['scenarios'],old):
            f,b,r=model(i,scenario)
            forecast_out.extend(dict(code=code,**row) for row in f)
            bridge_out.extend(dict(code=code,**row) for row in b)
            price=r['per_share'] if company=='moutai' else r['per_share_conservative_case']
            oldprice=D(previous['per_share'] if company=='moutai' else previous['per_share_conservative_case'])
            results_out.append(dict(code=code,scenario=scenario['name'],per_share=price,pdf_model_per_share=oldprice,difference=price-oldprice))
        for w in ['0.07','0.08','0.09','0.10']:
            s=assumptions['scenarios'][1]|{'wacc':w};r=model(i,s)[2]
            sensitivity_out.append(dict(code=code,wacc=w,per_share=r['per_share'] if company=='moutai' else r['per_share_conservative_case']))
    assert used_supplements==set(supplied), 'unused or missing declared supplements'
    # 不将不同模型的预测字段硬凑为同一标准模型；每家公司分别输出。
    outputs={'input-audit':list(audit.values()),'inputs':inputs_out,'valuation':results_out,'sensitivity':sensitivity_out}
    for code in ['600519','300866']:
        outputs[code+'-forecast']=[r for r in forecast_out if r['code']==code]
        outputs[code+'-equity-bridge']=[r for r in bridge_out if r['code']==code]
    for rows in outputs.values():
        for r in rows:
            for k,vv in list(r.items()):
                if isinstance(vv,D):r[k]=format(vv.quantize(D('.000001'),rounding=ROUND_HALF_UP),'f')
            r['information_as_of']=ASOF
            r['valuation_date']='2026-06-30'
            r.setdefault('status','illustrative_model_not_reported_fact')
    return outputs


def main(write=False):
    env=os.environ|{'GOPROXY':'off','GOSUMDB':'off'};env.pop('ALPHALAKE_WRITE_VALUATION_CHAIN',None)
    subprocess.run(['go','test','./internal/ingest','-run','^TestRealValuationStandardChain$','-count=1'],cwd=REPO,env=env,check=True)
    for script in ['anker-dcf-2026/verify.py','moutai-valuation-2026/verify.py']:
        subprocess.run([sys.executable,str(ROOT.parent/script)],cwd=REPO,check=True)
    module('earnings_wc_evidence', ROOT.parent/'earnings-working-capital-2026/verify.py').verify(write)
    module('financial_instruments_evidence', ROOT.parent/'financial-instruments-2026/verify.py').verify(write)
    module('balance_profit_evidence', ROOT.parent/'balance-profit-2026/verify.py').verify(write)
    module('cash_rd_evidence', ROOT.parent/'cash-rd-2026/verify.py').verify(write)
    module('supplement_review', ROOT.parent/'supplement-review-2026/verify.py').verify(write)
    with localcontext() as ctx:
        ctx.prec=40
        for name,rows in build().items():
            expected=csv_text(rows);p=ROOT/(name+'.csv')
            if write:p.write_text(expected)
            else:assert p.read_text()==expected,name+' differs'
    module('supplement_negative', ROOT.parent/'supplement-review-2026/negative.py').verify()
    print('通过：两家公司生产标准链、PDF 对账/显式补充、模型及差异表。')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--write',action='store_true');main(p.parse_args().write)
