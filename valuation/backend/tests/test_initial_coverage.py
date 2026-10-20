"""研究入口保留来源身份、单位、期间、未知与零，不执行500家研究或触网。"""
import gzip
import json

import pytest

from tools.initial_coverage import business_data
from tools.initial_coverage_report import cash_display, disclosed_points, guidance_rows
from datetime import date


def packet():
    identity=dict(SECUCODE='600519.SH',SECURITY_CODE='600519')
    return dict(zyfw=[identity|dict(BUSINESS_SCOPE='合成业务；不是真实公司金额')],
        zygcfx=[identity|dict(REPORT_DATE='2026-06-30 00:00:00',MAINOP_TYPE='2',ITEM_NAME='测试产品',MAIN_BUSINESS_INCOME=0,MBI_RATIO=0,GROSS_RPOFIT_RATIO=None),
                identity|dict(REPORT_DATE='2026-06-30 00:00:00',MAINOP_TYPE='3',ITEM_NAME='测试地区',MAIN_BUSINESS_INCOME=None),
                identity|dict(REPORT_DATE='2026-12-31 00:00:00',MAINOP_TYPE='2',ITEM_NAME='未来占位',MAIN_BUSINESS_INCOME=99)],
        jyps=[identity|dict(REPORT_DATE='2026-06-30 00:00:00',BUSINESS_REVIEW='公司计划未来收入达到200万元，但尚未实现，不应当作实际业绩。')])


def test_gzipped_response_is_the_same_company_evidence_and_periods_are_not_mixed():
    raw=json.dumps(packet(),ensure_ascii=False).encode()
    plain=business_data(raw,'SHSE:600519','2026-06-30','2026-10-20')
    assert business_data(gzip.compress(raw),'SHSE:600519','2026-06-30','2026-10-20')==plain
    assert [(r['dimension'],r['revenue_cny']) for r in plain['segments']]==[('product',0),('region',None)]
    assert plain['segments'][0]['revenue_share']==0 and plain['segments'][0]['gross_margin'] is None
    assert plain['monetary_unit']=='CNY'
    assert disclosed_points(plain)[0]['role']=='公司计划，尚未兑现'


@pytest.mark.parametrize('bad', [True,float('nan'),float('inf'),'0'])
def test_invalid_business_amounts_are_not_coerced_to_zero(bad):
    data=packet();data['zygcfx'][0]['MAIN_BUSINESS_INCOME']=bad
    with pytest.raises(ValueError,match='金额'):
        business_data(json.dumps(data).encode(),'SHSE:600519','2026-06-30','2026-10-20')


def test_wrong_identity_empty_and_decompression_bomb_are_rejected():
    data=packet();data['zygcfx'][0]['SECUCODE']='600000.SH'
    with pytest.raises(ValueError,match='身份'):
        business_data(json.dumps(data).encode(),'SHSE:600519','2026-06-30','2026-10-20')
    with pytest.raises(ValueError,match='为空'):
        business_data(b'{"zyfw":[],"zygcfx":[],"jyps":[]}','SHSE:600519','2026-06-30','2026-10-20')
    with pytest.raises(ValueError,match='解压'):
        business_data(gzip.compress(b'x'*((8<<20)+1)),'SHSE:600519','2026-06-30','2026-10-20')


def test_newer_operating_report_enters_story_without_changing_financial_base():
    data=packet()
    data['jyps'].append(dict(SECUCODE='600519.SH',SECURITY_CODE='600519',REPORT_DATE='2026-09-30',BUSINESS_REVIEW='前三季度收入100万元，新的经营证据应保留，财务基期仍然是中报。'))
    parsed=business_data(json.dumps(data).encode(),'SHSE:600519','2026-06-30','2026-10-20')
    assert parsed['report_end']=='2026-06-30' and parsed['information_date']=='2026-10-20'
    assert [r['period'] for r in parsed['reviews']]==['2026-06-30','2026-09-30']
    assert all(r['period']<='2026-10-20' for r in parsed['segments'])
    assert disclosed_points(parsed)[0]['period']=='2026-09-30'


def test_cash_chart_uses_comparable_half_year_when_annual_mapping_window_is_short():
    facts={p:dict(values_cny={'net_income_parent_ytd':str(net),'operating_cash_flow_cumulative':str(cfo)})
           for p,net,cfo in [('2025-12-31',10,11),('2025-06-30',5,0),('2026-06-30',6,7)]}
    periods,series,kind=cash_display(facts,2025,date(2026,6,30))
    assert periods==['2025-06-30','2026-06-30'] and kind=='bar'
    assert series[1]['values']==[0,7/1e8]
    facts['2024-12-31']=dict(values_cny={'net_income_parent_ytd':'1','operating_cash_flow_cumulative':'2'})
    periods,series,kind=cash_display(facts,2025,date(2026,6,30))
    assert len(periods)==5 and kind=='line' and all(p.endswith('12-31') for p in periods)
    assert series[0]['values'][0] is None


def test_guidance_metadata_is_not_a_zero_interval_or_a_formal_fact():
    row=dict(source='cninfo',period_end='2026-09-30',semantic_status='valid',announcement_date='2026-10-20',first_observed_at=None,title='合成公司业绩预告')
    display=guidance_rows([row])[0]
    assert '未给结构化金额' in display[1] and '0.00' not in display[1]
    row.update(forecast_parent_net_income_lower=0,forecast_parent_net_income_upper=10,forecast_parent_net_income_unit='CNY')
    assert '0.00～10.00 CNY' in guidance_rows([row])[0][1]
