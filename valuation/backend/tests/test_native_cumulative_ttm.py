"""标准累计→SQLite→原生输入：与季度路径等价，缺季度不伪造，缺组成不放行。"""
from copy import deepcopy
from datetime import date, datetime
import json
import sqlite3

import pytest

from api.database import _db_record_to_company_input
from data_sources import us_cn_hk_db as db
from engine.orchestrator import run_full_valuation
from tests.test_native_input_gates import sample
from tools.export_alphalake_sqlite import cell, export_snapshot
from tools.review_native_policy import review_report


def observation(period, field, value, *, basis=None, unit='CNY'):
    return dict(source='tdx', code='300866', instrument_id=7, period=period,
        field=field, canonical_field=field, value=str(value*1000000), unit=unit,
        period_type=basis or {3:'Q1',6:'H1',9:'9M',12:'FY'}[int(period[5:7])],
        statement_scope='provider_default', fact_id=period+'/'+field,
        available_at=None, artifact_sha256='a'*64)


@pytest.mark.parametrize('month,expected', [(3,12), (6,14), (9,16), (12,8)])
def test_ttm_uses_matching_cumulative_periods_not_previous_quarter(month, expected):
    end = date(2026,month,31 if month in (3,12) else 30)
    rows = [observation('2025-12-31','reported_ebit',10),
            observation(end.isoformat(),'reported_ebit',8)]
    if month != 12:
        rows.append(observation(end.replace(year=2025).isoformat(),'reported_ebit',8-month*2//3))
    facts = {(r['period'],r['field']):r for r in rows}
    value,status,terms = cell(facts,set(),7,end,'ebit',True,ttm=True)
    assert (value,status)==(expected,'available')
    assert [r['coefficient'] for r in terms] == ([1] if month==12 else [1,1,-1])
    if month!=12:
        facts.pop((end.replace(year=2025).isoformat(),'reported_ebit'))
        assert cell(facts,set(),7,end,'ebit',True,ttm=True)[:2]==(None,'missing_standard_fact')
    assert cell(facts,{end.isoformat()},7,end,'ebit',True,ttm=True)[1]=='source_record_conflict'
    facts[(end.isoformat(),'reported_ebit')]['unit']='USD'
    with pytest.raises(ValueError,match='unit'):
        cell(facts,set(),7,end,'ebit',True,ttm=True)


@pytest.fixture
def snapshot(monkeypatch):
    fetch_company = db.fetch_company
    sample(monkeypatch)  # 相同显式参考，不改变原生默认参数。
    monkeypatch.setattr(db, 'fetch_company', fetch_company)
    facts=[]
    for year in range(2020,2026):
        for field,value in [('revenue_cumulative',1000),('reported_ebit',150),
                            ('research_and_development_expense',50)]:
            facts.append(observation(f'{year}-12-31',field,value))
    for period,rev,ebit,rd in [('2025-03-31',180,25,9),('2025-06-30',400,60,20),
                              ('2025-09-30',700,100,35),('2026-03-31',220,35,12),
                              ('2026-06-30',500,80,30)]:
        for field,value in [('revenue_cumulative',rev),('reported_ebit',ebit),
                            ('research_and_development_expense',rd)]:
            facts.append(observation(period,field,value))
    for period in ['2025-12-31','2026-06-30']:
        for field,value in [('cash_and_cash_equivalents',50),('long_term_equity_investments',0),
                            ('equity_parent',300),('noncontrolling_interests',0),
                            ('short_term_borrowings',100),('long_term_borrowings',0),
                            ('bonds_payable',0),('current_portion_noncurrent_liabilities',0),
                            ('lease_liabilities',0)]:
            facts.append(observation(period,field,value,basis='instant'))
        facts.append(observation(period,'total_shares',10,basis='instant',unit='share'))
    asof=datetime.fromisoformat('2026-09-30T06:35:09+00:00')
    company=dict(symbols=['sz300866'],symbol_count=1,identifier_count=1,exchange_mic='XSHE',instrument_id=7,name='累计样本',
        quote=dict(close='10',trade_date='2026-09-29',recorded_at=asof.isoformat(),
                   acquisition_started_at=asof.isoformat(),run_finished_at=asof.isoformat()))
    def build(rows):
        conn=sqlite3.connect(':memory:'); conn.row_factory=sqlite3.Row
        def fetch(code,end):
            return dict(contract_version='alphalake-valuation-v2',code=code,report_period=end.isoformat(),
                information_as_of=asof.isoformat(),facts=[r for r in rows if r['period']<=end.isoformat()],source_conflicts=[])
        export_snapshot(conn,[company],fetch,date(2026,6,30),asof,years=6)
        conn.executemany('INSERT INTO metadata VALUES(?,?)', dict(report_period='2026-06-30',
            information_as_of=asof.isoformat(),source_database_sha256='source',annual_bv_equity='parent_attributable').items())
        return conn
    return facts,build


def test_complete_quarter_equivalence_and_missing_q1_still_values(snapshot):
    facts,build=snapshot
    with build(facts) as conn:
        record=db.fetch_company(conn,'SZSE:300866')
        assert db.native_compatibility(conn,'SZSE:300866')['status']=='ready'
        inputs,_=_db_record_to_company_input(record,.04,None)
        assert inputs.quarters_since_10k==0 and inputs.quarterly_financials==[]
        assert inputs.prepared_ttm.financials.revenues==1100
        assert inputs.prepared_ttm.financials.ebit==170
        assert inputs.prepared_ttm.financials.r_and_d_expense==60
        assert inputs.prepared_ttm.financials.consolidated_book_equity==300
        quarter_record=deepcopy(record); quarter_record.pop('data_source')
        old,_=_db_record_to_company_input(quarter_record,.04,None)
        # 外部季度源无合并范围元数据；等价比较显式保持同一资本范围。
        for row in old.raw_financials+old.quarterly_financials:
            row.consolidated_book_equity=row.bv_equity
        before=run_full_valuation(old); after=run_full_valuation(inputs)
        assert after.ltm_financials.model_dump()==before.ltm_financials.model_dump()
        assert after.adjusted==before.adjusted and after.dcf==before.dcf and after.final==before.final
        # review_report消费完整原生报告，不从quarterly空列表误降级为年度增长。
        from api.session_store import create_session
        from api.routes import _get_damodaran_store, _report_to_dict
        body=_report_to_dict(create_session(inputs,after,reference_store=_get_damodaran_store()))
        recent=review_report(body)['model_input_recent_window']
        assert recent['quarters']==2 and recent['revenue_growth']==.25
        assert json.loads(inputs.prepared_ttm.provenance['components'])['revenues'][1]['period']=='2026-06-30'
    without_q1=[r for r in facts if r['period'] not in ('2025-03-31','2026-03-31')]
    with build(without_q1) as conn:
        assert db.native_compatibility(conn,'SZSE:300866')['status']=='ready'
        record=db.fetch_company(conn,'SZSE:300866')
        assert record['financials_quarterly'][0]['revenues'] is None
        inputs,_=_db_record_to_company_input(record,.04,None)
        assert run_full_valuation(inputs).final==after.final
        conn.execute("UPDATE export_cells SET status='source_record_conflict' WHERE series='ttm' AND field='ebit'")
        assert any(r['field']=='ebit' for r in db.native_compatibility(conn,'SZSE:300866')['required_missing'])
        conn.execute('DELETE FROM financials_ttm')
        with pytest.raises(ValueError,match='Missing TTM'):
            db.fetch_company(conn,'SZSE:300866')


def test_missing_cumulative_and_research_queue_remain_distinct(snapshot):
    facts,build=snapshot
    missing=[r for r in facts if not (r['period']=='2025-06-30' and r['field'] in ('reported_ebit','research_and_development_expense'))
             and not (r['period']=='2022-12-31' and r['field']=='research_and_development_expense')]
    with build(missing) as conn:
        gate=db.native_compatibility(conn,'SZSE:300866')
        assert any(r['series']=='ttm' and r['field']=='ebit' for r in gate['required_missing'])
        assert {(r['series'],r['field']) for r in gate['conditional_missing']}=={('ttm','r_and_d_expense'),('annual','r_and_d_expense')}
        conn.execute("UPDATE metadata SET value='alphalake-sqlite-v9' WHERE key='contract'")
        with pytest.raises(ValueError,match='Unsupported'):
            db.fetch_company(conn,'SZSE:300866')
