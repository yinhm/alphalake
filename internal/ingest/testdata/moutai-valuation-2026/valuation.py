"""茅台专用预测政策；不把不完整的历史酒业现金流填成事实。"""
import json
from decimal import Decimal as D,localcontext,ROUND_HALF_UP
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def near(a,b):
    assert abs(a-b)<D('0.000001'),(a,b)


def model(i,scenario):
    s={k:D(v) for k,v in scenario.items() if k!='name'}
    assert set(s)==set('volume_growth price_mix_growth terminal_growth margin tax_rate wacc terminal_roic inventory_ratio long_capital_ratio other_wc_ratio operating_cash_ratio financial_asset_recovery risk_asset_recovery finance_pb minority_scale'.split())
    assert 0<=s['terminal_growth']<s['wacc']<1 and s['terminal_growth']<s['terminal_roic']<=1
    assert 0<=s['volume_growth']<D('.5') and 0<=s['price_mix_growth']<D('.5')
    assert 0<s['margin']<1 and 0<s['tax_rate']<1
    assert 0<s['inventory_ratio']<1 and 0<s['long_capital_ratio']<1 and -1<s['other_wc_ratio']<1
    assert 0<=s['operating_cash_ratio']<1 and 0<=s['financial_asset_recovery']<=1 and 0<=s['risk_asset_recovery']<=1
    assert 0<=s['finance_pb']<=3 and 0<s['minority_scale']<=3
    assert i['shares']>0
    minority=i['operating_minority_fraction_proxy']*s['minority_scale'];assert 0<=minority<1
    growth0=(1+s['volume_growth'])*(1+s['price_mix_growth'])-1
    revenue=[i['revenue_ttm']];growth=[]
    # 多算五年，只供储酒资金提前投入；并非额外折现五年现金流。
    for year in range(1,16):
        g=growth0 if year<=5 else growth0+(s['terminal_growth']-growth0)*D(min(year-5,5))/5
        growth.append(g);revenue.append(revenue[-1]*(1+g))
    inventory=i['inventory'];other_wc=i['other_wc'];forecast=[]
    for year in range(1,11):
        fade=D(min(year,5))/5
        margin=i['ebit_ttm']/revenue[0]+(s['margin']-i['ebit_ttm']/revenue[0])*fade
        delta_inventory=s['inventory_ratio']*(revenue[year+5]-revenue[year+4])
        inventory+=delta_inventory
        wc_ratio=i['other_wc']/revenue[0]+(s['other_wc_ratio']-i['other_wc']/revenue[0])*fade
        other_next=revenue[year]*wc_ratio;delta_other=other_next-other_wc;other_wc=other_next
        cash_increase=(revenue[year]-revenue[year-1])*s['operating_cash_ratio']
        net_long=s['long_capital_ratio']*(revenue[year+2]-revenue[year+1])
        da=revenue[year]*i['da_ttm']/revenue[0]
        nopat=revenue[year]*margin*(1-s['tax_rate'])
        investment=delta_inventory+delta_other+cash_increase+net_long
        fcff=nopat-investment
        near(fcff,nopat+da-(net_long+da)-delta_inventory-delta_other-cash_increase)
        forecast.append(dict(scenario=scenario['name'],year=year,revenue=revenue[year],growth=growth[year-1],ebit_margin=margin,nopat=nopat,
            inventory_balance=inventory,inventory_increase=delta_inventory,other_wc_balance=other_wc,other_wc_change=delta_other,operating_cash_increase=cash_increase,
            da=da,gross_longlived_investment=net_long+da,net_longlived_investment=net_long,reinvestment=investment,fcff=fcff,pv=fcff/(1+s['wacc'])**year))
    g=s['terminal_growth'];w=s['wacc']
    terminal_nopat=revenue[10]*(1+g)*s['margin']*(1-s['tax_rate'])
    terminal_investment=terminal_nopat*g/s['terminal_roic']
    # 稳态总再投资覆盖提前储酒和其他营运投入；余下才支持长期资产。
    terminal_inventory=s['inventory_ratio']*revenue[15]*g
    terminal_other=(s['other_wc_ratio']+s['operating_cash_ratio'])*revenue[10]*g
    assert terminal_investment>=terminal_inventory+terminal_other
    terminal_fcff=terminal_nopat-terminal_investment
    tv=terminal_fcff/(w-g);tv_pv=tv/(1+w)**10
    explicit=sum((r['pv'] for r in forecast),D(0));enterprise=explicit+tv_pv
    # 合并资产已经抵销内部存款，只减报表列示的外部存款；不再扣独立财务公司全部负债。
    financial_assets=i['financial_gross']*s['financial_asset_recovery']+i['risk_assets']*s['risk_asset_recovery']
    reserve=revenue[0]*s['operating_cash_ratio']
    residual=financial_assets-i['external_deposits']-i['finance_book_equity']-reserve-i['income_tax_payable']-i['lease_debt']
    assert residual>=0
    ordinary_operating=enterprise*(1-minority)
    ordinary_residual=residual*(1-minority)
    finance_value=i['finance_book_equity']*D('.51')*s['finance_pb']
    equity=ordinary_operating+ordinary_residual+finance_value
    bridge=[dict(scenario=scenario['name'],item=k,value=v) for k,v in [
        ('liquor_enterprise_proxy',enterprise),('operating_minority_proxy',-enterprise*minority),('financial_assets_after_haircut',financial_assets),
        ('external_deposits',-i['external_deposits']),('full_finance_book_equity_removed',-i['finance_book_equity']),('necessary_operating_cash',-reserve),
        ('existing_income_tax_payable',-i['income_tax_payable']),('lease_debt',-i['lease_debt']),('residual_financial_minority_allocation',-residual*minority),('owned_finance_equity_value',finance_value)]]
    near(sum((r['value'] for r in bridge),D(0)),equity)
    result=dict(scenario=scenario['name'],explicit_pv=explicit,terminal_nopat=terminal_nopat,terminal_reinvestment=terminal_investment,terminal_inventory_requirement=terminal_inventory,
        terminal_fcff=terminal_fcff,terminal_value=tv,terminal_pv=tv_pv,terminal_share=tv_pv/enterprise,enterprise_value=enterprise,operating_minority_fraction=minority,
        residual_financial_pool=residual,owned_finance_value=finance_value,equity_value=equity,shares=i['shares'],per_share=equity/i['shares'])
    return forecast,bridge,result


def verify_model(values,bridges,stocks):
    v=lambda p,k:values[p+'/'+k]
    ttm=lambda k:v('FY2025',k)+v('H12026',k)-v('H12025',k)
    b={r['period']:r for r in bridges};st=stocks[-1]
    base_ebit=D(b['FY2025']['liquor_ebit_proxy'])+D(b['H12026']['liquor_ebit_proxy'])-D(b['H12025']['liquor_ebit_proxy'])
    minority_income=ttm('minority_income')-ttm('finance_net_income')*D('.49')
    i=dict(revenue_ttm=ttm('revenue'),ebit_ttm=base_ebit,da_ttm=sum((ttm('cf_'+k) for k in ['fixed_da','rou_da','intangible_da','deferred_da']),D(0)),
        inventory=v('2026-06-30','inventory'),other_wc=D(st['other_wc_proxy']),financial_gross=D(st['financial_gross']),risk_assets=D(st['risk_investments']),
        external_deposits=v('2026-06-30','deposits_liability'),finance_book_equity=v('H12026','finance_equity'),income_tax_payable=v('2026-06-30','income_tax_payable'),
        lease_debt=D(st['lease_debt']),shares=v('2026-06-30','shares_close'),operating_minority_fraction_proxy=minority_income/(base_ebit*D('.75')))
    assert i['shares']==v('2026-06-30','shares') and i['shares']==v('2026-06-30','shares_open')+v('2026-06-30','shares_cancelled')
    sources={
        'revenue_ttm':'reported: FY2025 + H12026 - H12025 / revenue; excludes financial interest',
        'ebit_ttm':'financial-bridge: liquor_ebit_proxy FY + H1 - prior H1; unallocated finance costs remain',
        'da_ttm':'reported: cf_fixed_da + cf_rou_da + cf_intangible_da + cf_deferred_da, TTM; consolidated proxy',
        'inventory':'reported: 2026-06-30/inventory; carrying cost, not retail wine value',
        'other_wc':'balance-bridge: wc_proxy - inventory; includes unclassified nonfinancial-looking items',
        'financial_gross':'balance-bridge: financial_gross; consolidated balance, not cash equivalents added again',
        'risk_assets':'balance-bridge: funds + associates',
        'external_deposits':'reported: 2026-06-30/deposits_liability; external to listed consolidation',
        'finance_book_equity':'reported: H12026/finance_equity; source 0.01 hundred-million CNY = 1,000,000 CNY precision',
        'income_tax_payable':'reported: 2026-06-30/income_tax_payable; assumed settled outside forecast',
        'lease_debt':'balance-bridge: current_leases + leases; source statements show no conventional borrowings',
        'shares':'reported: shares_close, shares_open + shares_cancelled; cancelled shares not deducted twice',
        'operating_minority_fraction_proxy':'(TTM consolidated minority income - 49% * rounded finance standalone TTM net income)/(liquor EBIT proxy * 75%); allocation assumption'
    }
    # 财务公司的权益亿元保留两位，即精度 100 万元，不是精确到元的经营分部拆分。
    assert set(sources)==set(i)
    a=json.loads((ROOT/'assumptions.json').read_text());assert a['valuation_date']=='2026-06-30' and a['information_as_of']=='2026-09-06T00:00:00+00:00'
    assert [s['name'] for s in a['scenarios']]==['cautious','central','expansive']
    forecasts=[];bridge_rows=[];summaries=[];sensitivity=[]
    with localcontext() as ctx:
        ctx.prec=40
        for s in a['scenarios']:
            f,b,r=model(i,s);forecasts+=f;bridge_rows+=b;summaries.append(r)
        base=a['scenarios'][1];f,b,r=model(i,base)
        for w in ['0.06','0.07','0.08','0.09','0.10']:
            for g in ['0.01','0.02','0.03']:
                s=base|{'wacc':w,'terminal_growth':g};z=model(i,s)[2]
                sensitivity.append(dict(test='wacc_growth',parameter='wacc',assumption=w,terminal_growth=g,per_share=z['per_share']))
        for k,choices in [('inventory_ratio',['0.30','0.36','0.45','0.60']),('long_capital_ratio',['0.10','0.20','0.30']),('other_wc_ratio',['-0.08','-0.05','0']),('finance_pb',['0','0.8','1','1.2']),('financial_asset_recovery',['0.90','0.95','1']),('risk_asset_recovery',['0','0.5','0.75','1']),('minority_scale',['0.75','1','1.25','1.5']),('margin',['0.60','0.65','0.68']),('tax_rate',['0.25','0.30']),('terminal_roic',['0.15','0.20','0.25'])]:
            for choice in choices:
                z=model(i,base|{k:choice})[2]
                sensitivity.append(dict(test='one_at_a_time',parameter=k,assumption=choice,terminal_growth=base['terminal_growth'],per_share=z['per_share']))
        independent=sum(float(x['fcff'])/(1+float(base['wacc']))**x['year'] for x in f)
        independent+=sum(float(r['terminal_fcff'])*(1+float(base['terminal_growth']))**n/(1+float(base['wacc']))**(11+n) for n in range(1000))
        assert abs(D(str(independent))-r['enterprise_value'])<D('0.01')
        assert r['terminal_reinvestment']>0 and r['terminal_fcff']<r['terminal_nopat'],'terminal growth requires reinvestment'
        assert model(i,base|{'wacc':'0.09'})[2]['per_share']<r['per_share']
        assert model(i,base|{'inventory_ratio':'0.45'})[2]['per_share']<r['per_share']
        assert model(i,base|{'minority_scale':'1.25'})[2]['per_share']<r['per_share']
        # 消除财务公司账面块再加 51%，不得在整个金融池之外额外重复加入。
        near(r['owned_finance_value'],i['finance_book_equity']*D('.51'))
        near(r['per_share']*i['shares'],r['equity_value'])
        assert f[0]['inventory_increase']==D(base['inventory_ratio'])*(f[5]['revenue']-f[4]['revenue']),'inventory funding must lead revenue by five years'
        for invalid in [{'wacc':'0.02'},{'terminal_roic':'0.01'},{'financial_asset_recovery':'1.1'},{'inventory_ratio':'-1'}]:
            try:model(i,base|invalid)
            except AssertionError:pass
            else:raise AssertionError(('invalid assumptions accepted',invalid))
        outputs={'model-inputs':[dict(item=k,value=v,source=sources[k]) for k,v in i.items()],'forecast':forecasts,'equity-bridge':bridge_rows,'valuation':summaries,'sensitivity':sensitivity}
        for rows in outputs.values():
            for row in rows:
                for k,vv in list(row.items()):
                    if isinstance(vv,D):row[k]=f"{vv.quantize(D('0.000001'),rounding=ROUND_HALF_UP):.6f}"
                row['valuation_date']=a['valuation_date'];row['status']='illustrative_model_not_reported_fact'
    return outputs
