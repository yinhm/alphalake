"""安克回溯验证性 DCF；先复核财报，再核对证据、假设及所有输出。"""
import argparse
import importlib.util
import json
import re
import sys
from decimal import Decimal as D, localcontext, ROUND_HALF_UP
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('anker_financial_verify',ROOT/'../anker-valuation-2026/verify.py')
FINANCIAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FINANCIAL)


def near(a,b):
    assert abs(a-b) < D('0.000001'),(a,b)


def model(inputs,scenario):
    s = {k:([D(v) for v in x] if k=='growth' else D(x)) for k,x in scenario.items() if k!='name'}
    assert set(s)==set('growth margin tax_start tax_terminal sales_to_capital wc_ratio operating_cash_ratio wacc terminal_growth terminal_roic investment_recovery cash_other_recovery minority_multiple debt_multiple extra_dilution_rate'.split())
    assert len(s['growth'])==10 and all(D(0)<=g<D(1) for g in s['growth'])
    assert D(0)<=s['terminal_growth']<s['wacc']<D(1)
    assert s['terminal_growth']<s['terminal_roic']<=D(1)
    for k in ['margin','tax_start','tax_terminal','wc_ratio','operating_cash_ratio','investment_recovery','cash_other_recovery','extra_dilution_rate']:
        assert D(0)<=s[k]<=(D(1) if k.endswith('recovery') else D('0.99')),k
    assert s['sales_to_capital']>0 and 1/s['sales_to_capital']>s['wc_ratio']+s['operating_cash_ratio']
    assert s['minority_multiple']>=1 and s['debt_multiple']>=1
    rev0,ebit0,wc0 = (inputs[k] for k in ['revenue_ttm','ebit_ttm','opening_wc'])
    assert rev0>0 and inputs['shares']>0
    rev,wc = rev0,wc0
    da_ratio = inputs['da_ttm']/rev0
    forecast=[]
    for year,g in enumerate(s['growth'],1):
        before=rev
        rev *= 1+g
        fade=D(min(year,5))/5
        margin=ebit0/rev0+(s['margin']-ebit0/rev0)*fade
        tax=s['tax_start']+(s['tax_terminal']-s['tax_start'])*fade
        wc_ratio=wc0/rev0+(s['wc_ratio']-wc0/rev0)*fade
        wc_next=rev*wc_ratio
        delta_wc=wc_next-wc
        delta_cash=(rev-before)*s['operating_cash_ratio']
        # 总资本周转假设含营运资本及必要经营现金；年内投入、同年产生收入为模型时点政策。
        net_long=(rev-before)*(1/s['sales_to_capital']-s['wc_ratio']-s['operating_cash_ratio'])
        investment=net_long+delta_wc+delta_cash
        ebit=rev*margin
        nopat=ebit*(1-tax)
        da=rev*da_ratio
        fcff=nopat-investment
        factor=(1+s['wacc'])**year
        near(fcff,nopat+da-(net_long+da)-delta_wc-delta_cash)
        forecast.append(dict(scenario=scenario['name'],year=year,revenue=rev,growth=g,ebit_margin=margin,ebit=ebit,tax_rate=tax,nopat=nopat,wc_balance=wc_next,wc_change=delta_wc,operating_cash_increase=delta_cash,da=da,gross_longlived_investment=net_long+da,net_longlived_investment=net_long,reinvestment=investment,fcff=fcff,discount_factor=factor,pv=fcff/factor))
        wc=wc_next
    # 稳态再投资覆盖全部资本，不在其上再次扣营运资本/租赁投入。
    terminal_nopat=rev*(1+s['terminal_growth'])*s['margin']*(1-s['tax_terminal'])
    terminal_reinvestment=terminal_nopat*s['terminal_growth']/s['terminal_roic']
    terminal_fcff=terminal_nopat-terminal_reinvestment
    terminal_value=terminal_fcff/(s['wacc']-s['terminal_growth'])
    terminal_pv=terminal_value/(1+s['wacc'])**10
    explicit_pv=sum((r['pv'] for r in forecast),D(0))
    enterprise=explicit_pv+terminal_pv
    liquid_cash=inputs['cash_equivalents']+inputs['other_cash_candidate']*s['cash_other_recovery']
    reserve=rev0*s['operating_cash_ratio']
    assert liquid_cash>=reserve
    excess_cash=liquid_cash-reserve
    investments=inputs['financial_debt_assets']+inputs['risky_investments']*s['investment_recovery']
    minority=inputs['minority']*s['minority_multiple']
    debt=inputs['debt']*s['debt_multiple']
    option_book=inputs['convertible_equity_book']
    claims=inputs['other_claims']
    equity=enterprise+excess_cash+investments-debt-minority-option_book-claims
    shares=inputs['shares']*(1+s['extra_dilution_rate'])
    no_conversion=equity/shares
    # 转股只释放已扣的债券索偿，不新增现金；面值/转股价决定新股。
    conversion_shares=inputs['convertible_face']/inputs['conversion_price']
    converted_equity=equity+inputs['convertible_debt']*s['debt_multiple']+option_book
    converted_price=converted_equity/(shares+conversion_shares)
    bridge=[dict(scenario=scenario['name'],item=k,value=v) for k,v in [
        ('operating_enterprise_value',enterprise),('excess_cash',excess_cash),('financial_assets_after_haircut',investments),('debt_claim_proxy',-debt),('minority_claim_proxy',-minority),('convertible_option_book_proxy',-option_book),('existing_other_claims',-claims)]]
    near(sum((r['value'] for r in bridge),D(0)),equity)
    result=dict(scenario=scenario['name'],explicit_pv=explicit_pv,terminal_nopat=terminal_nopat,terminal_reinvestment=terminal_reinvestment,terminal_fcff=terminal_fcff,terminal_value=terminal_value,terminal_pv=terminal_pv,terminal_share=terminal_pv/enterprise,enterprise_value=enterprise,equity_no_conversion=equity,shares_with_award_stress=shares,conversion_shares=conversion_shares,equity_if_converted=converted_equity,per_share_no_conversion=no_conversion,per_share_if_converted=converted_price,per_share_conservative_case=min(no_conversion,converted_price))
    return forecast,bridge,result


def main(write=False):
    values,texts,results=FINANCIAL.main()  # 默认只读，不能通过 DCF --write 重写历史事实。
    raw=json.loads((ROOT/'evidence.json').read_text())
    assert len(raw)==20
    extra={}
    for r in raw:
        page=texts[r['pdf_id']].split(f"=== 页 {r['pdf_page']} ===\n",1)[1].split('=== 页 ',1)[0]
        assert page.count(r['quote'])==1,r
        tokens=re.findall(r'-?[\d,]+\.\d{2}(?!\d)',r['quote'])
        for c in r['cells']:
            assert c['key'] not in extra and tokens[c['token']]==c['printed'],c
            extra[c['key']]=D(tokens[c['token']].replace(',',''))
            assert extra[c['key']]==D(c['value']),c
    assert len(extra)==35
    # 仅本报告转债表的数值跨行；锁原文后合并两段，不推广成通用 PDF 规则。
    face_excerpt,=[r['quote'] for r in raw if r['quote'].startswith('11,048,200 ')]
    assert '1,104,660,8\n00.00' in face_excerpt
    face=D(''.join(re.search(r'(1,104,660,8)\n(00\.00)',face_excerpt).groups()).replace(',',''))
    assert face+D('159200.00')==D('1104820000.00')
    v=lambda k:values['2026-06-30/'+k]
    amount=lambda name,item,period: next(D(r['value']) for r in results[name] if r['item']==item and r['period']==period)
    ttm=lambda k: values['2025-12-31/'+k]+values['2026-06-30/'+k]-values['2025-06-30/'+k]
    assert extra['shares_open']+extra['shares_issued']==extra['shares_close']==extra['share_capital']
    assert extra['weighted_basic_shares']+extra['weighted_award_dilution']+extra['weighted_bond_dilution']==extra['weighted_diluted_shares']
    assert extra['current_financial_debt']+extra['current_financial_equity']+v('trading_forward_asset')==v('trading_assets')
    assert extra['noncurrent_financial_equity']+extra['noncurrent_financial_debt']==extra['noncurrent_financial_total']
    assert extra['project_cip_open']+extra['project_cip_add']==extra['project_transfer']==extra['buildings_added_2025']
    assert extra['buildings_gross_2025_open']+extra['buildings_added_2025']==extra['buildings_gross_h1_open']
    assert extra['buildings_gross_h1_open']+extra['buildings_added_h1']==extra['buildings_gross_h1_close']
    assert extra['buildings_accumulated_da_open']+extra['buildings_da_h1']+extra['buildings_da_fx']==extra['buildings_accumulated_da_close']
    inputs=dict(revenue_ttm=ttm('revenue'),ebit_ttm=amount('ebit','ebit_financing_and_investment_adjusted','TTM-2026-06-30'),
        opening_wc=amount('working-capital','wc_after_identified_exclusions','2026-06-30'),
        da_ttm=sum((ttm(k) for k in ['cf_property_depreciation','cf_fixed_depreciation','cf_rou_depreciation','cf_intangible_amortization','cf_deferred_amortization']),D(0)),
        cash_equivalents=extra['cash_equivalents'],other_cash_candidate=v('cash')-extra['cash_equivalents']-extra['restricted_cash'],
        financial_debt_assets=extra['current_financial_debt']+extra['noncurrent_financial_debt']+v('deposits'),
        risky_investments=extra['current_financial_equity']+extra['noncurrent_financial_equity']+extra['associates']+v('loan_receivable')-v('loan_allowance'),
        minority=v('minority'),debt=amount('debt','interest_bearing_debt_book_value','2026-06-30'),
        other_claims=sum((v(k) for k in ['income_tax_payable','repurchase_payable','capex_payable','ipo_payable']),D(0))+extra['provisions'],
        shares=extra['shares_close'],convertible_debt=v('all_bonds'),convertible_equity_book=extra['convertible_equity_book'],convertible_face=face,conversion_price=extra['conversion_price'])
    assert inputs['other_cash_candidate']>=0
    assert inputs['shares']==extra['share_capital'],'report-date share count must not use post-balance-sheet shares'
    assert '(每股面值1 元)' in texts['1225533054'].split('=== 页 125 ===\n',1)[1].split('=== 页 ',1)[0]
    sources={
        'revenue_ttm':'reported.csv: FY2025 + H12026 - H12025 / revenue',
        'ebit_ttm':'ebit.csv: TTM-2026-06-30 / ebit_financing_and_investment_adjusted',
        'opening_wc':'working-capital.csv: 2026-06-30 / wc_after_identified_exclusions; provisional',
        'da_ttm':'reported.csv: five depreciation/amortization components; FY + H1 - prior H1',
        'cash_equivalents':'evidence.json: cash_equivalents; 1225533054 p139',
        'other_cash_candidate':'reported.csv: cash - evidence.cash_equivalents - evidence.restricted_cash; not proved freely available',
        'financial_debt_assets':'evidence: current_financial_debt + noncurrent_financial_debt + reported.deposits; book proxy, not guaranteed cash',
        'risky_investments':'evidence: current/noncurrent financial equity + associates + reported.loan_receivable - loan_allowance; before scenario haircut',
        'minority':'reported.csv: 2026-06-30 / minority; book proxy before scenario multiple',
        'debt':'debt.csv: interest_bearing_debt_book_value; book proxy before scenario multiple',
        'other_claims':'reported: income_tax_payable + repurchase_payable + capex_payable + ipo_payable + evidence.provisions; assumed existing cash claims outside forecast',
        'shares':'evidence: shares_close; 1225533054 p125; one CNY par per ordinary share, not weighted EPS shares',
        'convertible_debt':'reported.csv: 2026-06-30 / all_bonds; current + noncurrent exactly once',
        'convertible_equity_book':'evidence: convertible_equity_book; book proxy, not option fair value',
        'convertible_face':'evidence: 1225533054 p54 split-line outstanding face amount',
        'conversion_price':'evidence: conversion_price; effective 2026-05-26'
    }
    assert set(sources)==set(inputs)
    assumptions=json.loads((ROOT/'assumptions.json').read_text())
    assert assumptions['valuation_date']=='2026-06-30' and assumptions['information_as_of']==FINANCIAL.AS_OF
    scenarios=assumptions['scenarios']
    assert [s['name'] for s in scenarios]==['cautious','central','expansive']
    forecasts=[]; bridges=[]; summaries=[]
    with localcontext() as ctx:
        ctx.prec=40
        for s in scenarios:
            f,b,r=model(inputs,s); forecasts+=f; bridges+=b; summaries.append(r)
        base=scenarios[1]
        sensitivities=[]
        for w in ['0.07','0.08','0.09','0.10','0.11']:
            for g in ['0.01','0.02','0.03','0.04']:
                s=base|{'wacc':w,'terminal_growth':g}
                r=model(inputs,s)[2]
                sensitivities.append(dict(test='wacc_terminal_growth',parameter='wacc',assumption=w,terminal_growth=g,per_share=r['per_share_conservative_case']))
        for key,choices in [('wc_ratio',['0.10','0.12','0.15','0.18']),('margin',['0.085','0.10','0.115']),('sales_to_capital',['2','3','4']),('tax_terminal',['0.15','0.20','0.25']),('extra_dilution_rate',['0','0.01','0.03','0.05']),('investment_recovery',['0','0.50','0.75','1']),('cash_other_recovery',['0','0.50','1']),('minority_multiple',['1','1.5','3']),('debt_multiple',['1','1.10','1.20']),('terminal_roic',['0.09','0.12','0.15'])]:
            for choice in choices:
                r=model(inputs,base|{key:choice})[2]
                sensitivities.append(dict(test='one_at_a_time',parameter=key,assumption=choice,terminal_growth=base['terminal_growth'],per_share=r['per_share_conservative_case']))
        # 独立数值路径：有限期显式折现 + 1000 期稳态现金流，避免仅复写同一终值公式。
        f,_,r=model(inputs,base)
        w=float(base['wacc']);g=float(base['terminal_growth'])
        independent=sum(float(x['fcff'])/(1+w)**x['year'] for x in f)
        independent+=sum(float(r['terminal_fcff'])*(1+g)**n/(1+w)**(11+n) for n in range(1000))
        assert abs(D(str(independent))-r['enterprise_value'])<D('0.01')
        # 边界、单调性与证券桥接；每次验收都执行。
        assert model(inputs,base|{'wacc':'0.10'})[2]['enterprise_value']<r['enterprise_value']
        assert model(inputs,base|{'wc_ratio':'0.15'})[2]['enterprise_value']<r['enterprise_value']
        assert model(inputs,base|{'extra_dilution_rate':'0.05'})[2]['per_share_conservative_case']<r['per_share_conservative_case']
        for invalid in [{'wacc':'0.03'},{'terminal_roic':'0.02'},{'sales_to_capital':'0'},{'investment_recovery':'-1'}]:
            try: model(inputs,base|invalid)
            except AssertionError: pass
            else: raise AssertionError(('invalid assumptions accepted',invalid))
        near(r['equity_if_converted']-r['equity_no_conversion'],inputs['convertible_debt']+inputs['convertible_equity_book'])
        assert r['terminal_reinvestment']>0 and r['terminal_fcff']<r['terminal_nopat'],'terminal growth requires reinvestment'
        near(r['per_share_no_conversion']*r['shares_with_award_stress'],r['equity_no_conversion'])
        near(r['per_share_if_converted']*(r['shares_with_award_stress']+r['conversion_shares']),r['equity_if_converted'])
        input_rows=[dict(item=k,value=v,source=sources[k]) for k,v in inputs.items()]
        for name,rows in [('inputs',input_rows),('forecast',forecasts),('equity-bridge',bridges),('valuation',summaries),('sensitivity',sensitivities)]:
            for row in rows:
                for k,vv in list(row.items()):
                    if isinstance(vv,D): row[k]=f"{vv.quantize(D('0.000001'),rounding=ROUND_HALF_UP):.6f}"
                row.update(valuation_date=assumptions['valuation_date'],information_as_of=FINANCIAL.AS_OF,status='illustrative_model_not_reported_fact')
            expected=FINANCIAL.csv_text(rows); path=ROOT/(name+'.csv')
            if write: path.write_text(expected)
            else: assert path.read_text()==expected,name+' differs; review policy before --write'
    print('通过：20 段新增原文、35 个金额及跨行转债面值；三情景、十年预测、股权桥接、敏感性及独立折现/无效参数检查。')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true',help='核对证据后重建本目录模型输出；不改历史事实')
    main(parser.parse_args().write)
