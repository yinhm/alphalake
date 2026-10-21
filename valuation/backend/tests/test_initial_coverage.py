"""研究入口保留来源身份、单位、期间、未知与零，不执行500家研究或触网。"""
import gzip
import hashlib
import io
import json
import sqlite3
import tarfile

import pytest

from tools.initial_coverage import business_data, save
from tools.initial_coverage_report import cash_display, disclosed_points, guidance_rows
from datetime import date


def test_research_json_preserves_decimal_evidence_zero_unknown_and_prior_file_on_bad_value(tmp_path):
    path = tmp_path/'valuation.json'
    payload = {'evidence': '1234567890123456789.12', 'source_zero': 0, 'unknown': None,
               'forecast': [0.125, -2.5, {'label': '条件值'}]}
    save(path, payload)
    assert json.loads(path.read_text()) == payload
    before = path.read_bytes()
    with pytest.raises(ValueError):
        save(path, {'invalid': float('nan')})
    assert path.read_bytes() == before


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


def write_archive(root, rows, snapshot, *, omitted=None, corrupt=None):
    """小型真实tar证据，既有成功公司也有不适用公司。"""
    from tools.initial_coverage import digest, save
    path=root/'candidate-check.tar.gz'
    entries={r['ticker'].replace(':','-')+'.json':json.dumps(r).encode() for r in rows}
    entries['summary.json']=json.dumps(dict(sha256=snapshot,companies=len(rows))).encode()
    expected={name:hashlib.sha256(raw).hexdigest() for name,raw in entries.items()}
    with tarfile.open(path,'w:gz') as archive:
        for name,raw in entries.items():
            if name==omitted:
                continue
            if name==corrupt:
                raw=raw+b' '
            entry=tarfile.TarInfo(name);entry.size=len(raw)
            archive.addfile(entry,io.BytesIO(raw))
    save(root/'candidate-check.archive.json',dict(sha256=digest(path),files=expected))
    return path


@pytest.fixture
def frozen_research(tmp_path,monkeypatch):
    from tools import initial_coverage as tool
    from tools.check_native_sqlite import value_digest
    from data_sources import us_cn_hk_db
    monkeypatch.setenv('ALPHALAKE_WORKSPACE',str(tmp_path))
    database=tmp_path/'valuation.sqlite'
    with sqlite3.connect(database) as c:
        c.execute('CREATE TABLE metadata(key TEXT,value TEXT)')
        c.execute("INSERT INTO metadata VALUES('reference_snapshot_id','reference-v1')")
        c.execute('CREATE TABLE companies(ticker TEXT,stock_price_listing REAL)')
        c.executemany('INSERT INTO companies VALUES(?,?)',[('SHSE:600004',7.79),('SHSE:600000',9.76)])
        c.execute('CREATE TABLE financials_annual(ticker TEXT,fy_offset INTEGER,revenues REAL,ebit REAL,net_income REAL,capex REAL,r_and_d_expense REAL,bv_equity REAL)')
        c.executemany('INSERT INTO financials_annual VALUES(?,0,?,NULL,NULL,NULL,NULL,NULL)',
                      [('SHSE:600004',0),('SHSE:600000',None)])
    source=tmp_path/'alphalake.duckdb';source.write_bytes(b'synthetic source, not a production database')
    snapshot=tool.digest(database)
    diagnostic=dict(status='ready')
    body=dict(ticker='SHSE:600004',inputs=dict(ticker='SHSE:600004'),reference_snapshot=dict(id='reference-v1'),
              dcf=dict(value=17),final=dict(value_per_share=17))
    rows=[dict(ticker='SHSE:600004',admission='ready',financial_admission='ready',http_status=200,
               diagnostic=diagnostic,response=body,missing_fields=[]),
          dict(ticker='SHSE:600000',admission='outside_product_scope',financial_admission='ready',http_status=422,
               diagnostic=dict(status='outside_product_scope'),response=dict(detail='金融模型暂停'),missing_fields=[])]
    archive=write_archive(tmp_path,rows,snapshot)
    runtime=dict(stages=dict(calculation='same-calculation',review='same-review'),versions={},files={})
    monkeypatch.setattr('tools.publish_native_valuation.runtime_identity',lambda _:runtime)
    checked=[{key:row[key] for key in ('ticker','admission','financial_admission','http_status','missing_fields')} |
        dict(inputs_sha256=value_digest(row['response'].get('inputs')),diagnostic_sha256=value_digest(row['diagnostic'])) for row in rows]
    publication=tmp_path/'delivery.json'
    tool.save(publication,dict(contract='alphalake-native-delivery-v2',published_sha256=snapshot,
        runtime_identity=runtime,check=dict(companies=2,sha256=snapshot,results=checked)))
    scope=[dict(ticker=row['ticker'],instrument_id=i+1,name='测试'+str(i)) for i,row in enumerate(rows)]
    monkeypatch.setattr(us_cn_hk_db,'publication_scope',lambda _:scope)
    monkeypatch.setattr(us_cn_hk_db,'snapshot_metadata',lambda _:dict(source_database_sha256=tool.digest(source),
        report_period='2026-06-30',information_as_of='2026-10-20T14:58:53+00:00',reference_snapshot_id='reference-v1'))
    contexts=[dict(ticker=row['ticker'],instrument_id=row['instrument_id'],model_scope=dict(industry='测试行业'),
                   market=dict(quote=dict(trade_date='2026-10-20'))) for row in scope]
    monkeypatch.setattr(us_cn_hk_db,'input_contexts',lambda _:iter(contexts))
    files=[]
    for name in ('profiles.json','questions.json','filings.json'):
        p=tmp_path/name;tool.save(p,{});files.append(p)
    protocol=tmp_path/'research-work/protocol.json'
    tool.freeze(protocol,*files,archive_file=archive,publication_file=publication)
    return protocol,rows,scope,runtime


def test_freeze_keeps_full_scope_versions_and_canonical_identity(frozen_research):
    from tools.initial_coverage import load_protocol,verify_inputs
    path,rows,scope,_=frozen_research
    protocol=load_protocol(path)
    assert protocol['source_universe']==2 and protocol['target']==2
    assert [(r['ticker'],r['instrument_id']) for r in protocol['companies']]==[(r['ticker'],r['instrument_id']) for r in scope]
    assert [r['admission'] for r in protocol['companies']]==['ready','outside_product_scope']
    assert protocol['information_as_of']=='2026-10-20T14:58:53+00:00'
    assert protocol['companies'][0]['quote_date']=='2026-10-20'
    verify_inputs(protocol,runtime=True)


def test_archive_keeps_rejection_and_rejects_tampered_or_missing_members(frozen_research):
    from tools.initial_coverage import archive_rows,digest,load_protocol
    path,rows,_,_=frozen_research;protocol=load_protocol(path)
    assert list(archive_rows(protocol))==rows
    for changed in (dict(corrupt='SHSE-600004.json'),dict(omitted='SHSE-600000.json')):
        archive=write_archive(path.parent.parent,rows,protocol['sqlite_sha256'],**changed)
        protocol['calculation_archive_sha256']=digest(archive)
        with pytest.raises(ValueError,match='哈希|缺少'):
            list(archive_rows(protocol))


def test_old_snapshot_or_wrong_ticker_cannot_be_current_research(frozen_research):
    from tools.initial_coverage import archive_rows,digest,load_protocol
    path,rows,_,_=frozen_research;protocol=load_protocol(path)
    archive=write_archive(path.parent.parent,rows,'old-snapshot')
    protocol['calculation_archive_sha256']=digest(archive)
    with pytest.raises(ValueError,match='发布快照'):
        list(archive_rows(protocol))
    rows[0]['response']['inputs']['ticker']='SHSE:600000'
    archive=write_archive(path.parent.parent,rows,protocol['sqlite_sha256'])
    protocol['calculation_archive_sha256']=digest(archive)
    with pytest.raises(ValueError,match='身份'):
        list(archive_rows(protocol))


def test_frozen_evidence_or_runtime_change_requires_refreeze(frozen_research):
    from tools.initial_coverage import load_protocol,verify_inputs
    path,_,_,runtime=frozen_research;protocol=load_protocol(path)
    runtime['stages']['calculation']='changed-engine'
    with pytest.raises(ValueError,match='依赖'):
        verify_inputs(protocol,runtime=True)
    (path.parent.parent/'profiles.json').write_text('{"changed":true}')
    with pytest.raises(ValueError,match='输入已变化'):
        verify_inputs(protocol)


def test_rejection_reason_must_match_current_published_diagnostic(frozen_research):
    from tools.initial_coverage import archive_rows,digest,load_protocol
    path,rows,_,_=frozen_research;protocol=load_protocol(path)
    rows[1]['diagnostic']['status']='unrelated_old_diagnostic'
    archive=write_archive(path.parent.parent,rows,protocol['sqlite_sha256'])
    protocol['calculation_archive_sha256']=digest(archive)
    with pytest.raises(ValueError,match='准入状态变化'):
        list(archive_rows(protocol))


def test_calculate_replays_ready_once_and_never_fabricates_financial_valuation(frozen_research,monkeypatch):
    from tools import initial_coverage as tool
    path,rows,_,_=frozen_research
    calls=[]
    class Client:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr('fastapi.testclient.TestClient',Client)
    def calculate(client,request,metadata,web=None,*,source_context=None):
        assert web is None and metadata['reference_snapshot_id']=='reference-v1'
        calls.append(request)
        return source_context,dict(economic_checks=dict(cashflow_bridge=dict(status='arithmetic_reconciled')))
    monkeypatch.setattr('tools.evaluate_native_policy.calculate_request',calculate)
    result=tool.calculate(path)
    assert len(calls)==1 and result['all_ready_api_verified'] and not result['all_live_verified']
    protocol=tool.load_protocol(path)
    ready=json.loads((tool.report_folder(rows[0]['ticker'],protocol)/'valuation.json').read_text())
    blocked=json.loads((tool.report_folder(rows[1]['ticker'],protocol)/'valuation.json').read_text())
    assert ready['api_verified'] and not ready['live_verified'] and ready['sensitivities']==[]
    assert blocked['status']=='outside_product_scope' and blocked['request'] is None and 'review' not in blocked
    assert blocked['response']==dict(detail='金融模型暂停') and ready['run_id']!=blocked['run_id']
    tool.calculate(path)
    assert len(calls)==1


def test_collect_never_requests_financial_or_unknown_scope(frozen_research,monkeypatch):
    from tools import initial_coverage as tool
    path,_,_,_=frozen_research;calls=[]
    def collect_one(row,cache,known,end):
        calls.append(row['ticker']);return dict(ticker=row['ticker'],status='source_failed',reason='合成网络失败')
    monkeypatch.setattr(tool,'collect_one',collect_one)
    result=tool.collect(path)
    receipt=json.loads((path.parent/'business-collection.json').read_text())
    assert calls==['SHSE:600004'] and result['requested']==1 and result['collected']==0
    assert len(receipt['failures'])==1 and receipt['states']==[dict(ticker='SHSE:600000',status='not_requested',reason='outside_product_scope')]


@pytest.mark.parametrize('wrong_identity',[False,True])
def test_facts_use_one_canonical_scope_and_bounded_company_jsonl(frozen_research,monkeypatch,wrong_identity):
    from pathlib import Path
    from tools import initial_coverage as tool
    path,_,_,_=frozen_research;commands=[]
    fact=dict(code='600004',instrument_id=99 if wrong_identity else 1,period='2025-12-31',field='revenue_cumulative',
        value='0',unit='CNY',period_type='FY',statement_scope='provider_default',source_record_id=17,
        artifact_sha256='a'*64,available_at=None,announcement_source=None,source_observed_at=None)
    def export(command,**kwargs):
        commands.append(command)
        assert '--codes' not in command
        scope=json.loads(Path(command[command.index('--scope-file')+1]).read_text())
        assert [r['instrument_id'] for r in scope]==[1,2]
        assert command[command.index('--as-of')+1]=='2026-10-20T14:58:53+00:00'
        out=Path(command[command.index('--output')+1]);out.mkdir()
        (out/'fields.jsonl').write_text('')
        (out/'conflicts.jsonl').write_text('')
        (out/'records.jsonl').write_text(json.dumps(fact)+'\n')
    monkeypatch.setattr(tool.subprocess,'run',export)
    monkeypatch.setattr('tools.export_alphalake_sqlite.record_facts',lambda stream,definitions:(json.loads(line) for line in stream))
    if wrong_identity:
        with pytest.raises(ValueError,match='身份'):
            tool.collect_facts(path)
        return
    result=tool.collect_facts(path)
    assert len(commands)==1 and result['facts']==1 and result['native_comparisons']==12
    manifest=json.loads((path.parent/'financial-facts.json').read_text())
    assert 'companies' not in manifest and 'annual' not in manifest and manifest['companies_count']==2
    data=tool.workspace_path(manifest['company_facts_file'])
    assert tool.digest(data)==manifest['company_facts_sha256']
    with data.open('rb') as stream:
        for ticker,(offset,length) in manifest['company_offsets'].items():
            stream.seek(offset);row=json.loads(stream.read(length))
            assert row['ticker']==ticker
            assert row['source_periods']['2025-12-31']['values_cny']==({'revenue_cumulative':'0'} if ticker=='SHSE:600004' else {})


def test_healthy_company_evidence_cache_is_reused_without_a_network_request(tmp_path,monkeypatch):
    from tools import initial_coverage as tool
    monkeypatch.setenv('ALPHALAKE_WORKSPACE',str(tmp_path))
    path=tmp_path/'business.json.gz';path.write_bytes(gzip.compress(json.dumps(packet()).encode()))
    monkeypatch.setattr(tool,'urlopen',lambda *a,**kw:pytest.fail('健康经营缓存不应重复下载'))
    known={'SHSE:600519':dict(path=path.name,sha256=tool.digest(path),fetched_at='2026-10-20T12:00:00+00:00')}
    cache=tmp_path/'cache';cache.mkdir()
    result=tool.collect_one(dict(ticker='SHSE:600519'),cache,known,'2026-06-30')
    assert result['status']=='collected' and result['retrieval']=='existing_cache'
    assert result['fetched_at']==known['SHSE:600519']['fetched_at']


def test_research_context_only_changes_explanation_and_keeps_unpublished_outside_denominator(frozen_research):
    from tools import initial_coverage as tool
    path,_,_,_=frozen_research;root=path.parent.parent
    evidence=root/'filings.json'
    reference=dict(path=evidence.name,sha256=tool.digest(evidence),locator='已核实资本变动与未发布身份')
    context=dict(contract='initial-coverage-research-context-v1',boundary='解释用，不改参数',
        company_context={'SHSE:600004':dict(price_comparison_status='unreconciled_post_period_capital_change',
            reason='财务基期后融资未成套接入',evidence=reference)},
        extra_source_candidates=[dict(ticker='SZSE:001381',name='合成未发布',instrument_id=3,
            report_id='initial-sz-001381-20261021',status='pending_snapshot_publication',reason='未发布',evidence=[reference])])
    context_path=root/'context.json';tool.save(context_path,context)
    output=root/'other-work/protocol.json'
    tool.freeze(output,root/'profiles.json',root/'questions.json',evidence,archive_file=root/'candidate-check.tar.gz',
                publication_file=root/'delivery.json',research_context=context_path)
    protocol=tool.load_protocol(output)
    assert protocol['target']==2 and len(protocol['companies'])==2
    assert protocol['company_context']==context['company_context']
    assert protocol['extra_source_candidates']==context['extra_source_candidates']
    context['company_context']['SHSE:600004']['inputs']={'valuation_assumptions':{}}
    tool.save(context_path,context)
    with pytest.raises(ValueError,match='输入已变化'):
        tool.verify_inputs(protocol)
    with pytest.raises(ValueError,match='字段'):
        tool.freeze(root/'bad-work/protocol.json',root/'profiles.json',root/'questions.json',evidence,
                    archive_file=root/'candidate-check.tar.gz',publication_file=root/'delivery.json',research_context=context_path)


def test_existing_guidance_is_bound_before_freeze_and_optional(frozen_research):
    from tools import initial_coverage as tool
    path,_,_,_=frozen_research;root=path.parent.parent
    assert 'guidance_file' not in tool.load_protocol(path)
    output=root/'with-guidance/protocol.json'
    guidance=output.parent/'guidance.json'
    tool.save(guidance,dict(contract_version='earnings-guidance-context-v1',companies=['SHSE:600004'],observations=[]))
    tool.freeze(output,root/'profiles.json',root/'questions.json',root/'filings.json',
                archive_file=root/'candidate-check.tar.gz',publication_file=root/'delivery.json')
    protocol=tool.load_protocol(output)
    assert protocol['guidance_file']=='with-guidance/guidance.json'
    assert protocol['protected'][protocol['guidance_file']]==tool.digest(guidance)
    guidance.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='输入已变化'):
        tool.verify_inputs(protocol)
