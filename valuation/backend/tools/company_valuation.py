"""统一公司估值入口：显式配置候选政策，复用批次校验与引擎，输出紧凑JSON。"""
import argparse
from datetime import datetime
from functools import cache
import json
import os
from pathlib import Path
import re
import subprocess

from data_sources.alphalake import content_hash
from tools.batch_valuate_alphalake import execution_error_reason, load_policy, run_batch

CONTRACT = 'alphalake-company-valuation-v1'
SUCCESS = {'illustrative_book_equity_scenario', 'illustrative_enterprise_value_only', 'illustrative_valuation_completed'}


def summarize_run(row, run_directory):
    """只读刚由共享入口保存的运行；完整请求与证据仍通过run ID追溯。"""
    path = Path(run_directory)/(row['run_id']+'.json')
    run = json.loads(path.read_text())
    if (run['run_id'] != row['run_id'] or run['status'] != row['status']
            or content_hash(dict(request=run['request'],engine_revision=run['engine_revision'])) != row['run_id']
            or run['report']['final']['value_per_share'] != row['value_per_share']):
        raise ValueError('saved run differs from evaluated candidate')
    policy = run['request']['policy']
    bridge = run['report']['equity_bridge']
    return dict(
        run_id=run['run_id'], engine_revision=run['engine_revision'],
        policy_id=policy['policy_id'], scenario=policy['scenario'],
        report_period=run['request']['data']['report_period'],
        information_as_of=run['request']['data']['information_as_of'],
        value_per_share=dict(value=row['value_per_share'],currency='CNY',unit='CNY/share'),
        operating_enterprise_value=dict(value=row['operating_enterprise_value_million_cny'],currency='CNY',unit='million_CNY'),
        wacc=run['report']['cost_of_capital']['wacc'],
        capital_structure_basis=run['report']['cost_of_capital'].get('capital_structure_basis'),
        capital_basis=policy.get('capital_basis',policy.get('bridge_basis','not_applicable')),
        # 不猜期后股数的日期；原始市场股本证据在运行请求中。
        share_date=(run['request']['data']['report_period'] if policy.get('capital_basis','financial_date')=='financial_date' and bridge else None),
        equity_bridge=bridge,
        assumptions=run['audit'].get('assumptions'),
        boundaries=run['audit'].get('boundaries'),
        historical_fcff_status=run['audit'].get('historical_fcff_status'),
        terminal_sensitivity=run.get('terminal_sensitivity'),
        evidence=dict(run_file=str(path.resolve()),request_sha256=content_hash(run['request'])),
    )


def company_valuation(readiness, code, policies, export, run_directory, select=None):
    if not re.fullmatch(r'\d{6}',code):
        raise ValueError('six-digit security code required')
    if readiness['universe_count']!=len(readiness['companies']):
        raise ValueError('universe count differs from company rows')
    if readiness['contract_version']!='alphalake-readiness-v1':
        raise ValueError('unsupported readiness contract')
    versions=[p.policy_version for p in policies]
    if not versions or len(set(versions))!=len(versions):
        raise ValueError('at least one uniquely versioned policy required')
    if select is not None and select not in versions:
        raise ValueError('selected policy version not supplied')
    matched=[c for c in readiness['companies'] if any(s[2:]==code for s in c['symbols'] or [])]
    output=dict(contract_version=CONTRACT,code=code,report_period=readiness['report_period'],
                information_as_of=readiness['information_as_of'],
                readiness_sha256=content_hash(readiness),universe_count=readiness['universe_count'],universe_scope=readiness['universe_scope'],
                selection=dict(policy_version=None,reason=None),valuation=None,candidates=[],
                boundary='条件估值；仅评估显式提供的政策集合，非当前目标价或自动政策审核。')
    if len(matched)!=1:
        output.update(status='blocked_security_identity',reason='security absent or ambiguous in local universe')
        return output
    company=matched[0]
    output['security']={k:company[k] for k in ('instrument_id','name','symbols','exchange_mic','financial_status','missing_core_fields')}
    if company.get('source_conflicts') or company['financial_status']=='blocked_security_identity':
        output.update(status='blocked_source_record_conflict' if company.get('source_conflicts') else 'blocked_security_identity',
                      reason='source or identity conflict precedes policy selection',source_conflicts=company.get('source_conflicts',[]))
        return output
    scoped=dict(readiness,companies=[company],universe_count=1)
    # 一次标准导出供同次候选计算复用，不跨运行缓存或修改来源。
    cached_export=cache(export)
    for policy in policies:
        row=run_batch(scoped,policy,cached_export)['companies'][0]
        route=row.get('policy_route') or {}
        assignment=policy.assignments.get(code)
        configured=assignment.policy if assignment else next((r.policy for r in policy.industry_rules if r.rule_id==route.get('rule_id')),None)
        candidate=dict(policy_version=policy.policy_version,policy_sha256=content_hash(policy.model_dump(mode='json')),
                       kind='company_assignment' if code in policy.assignments else 'industry_rules',
                       configured_policy=configured.model_dump(mode='json') if configured else None,
                       status=row['status'],policy_route=row.get('policy_route'),
                       reason=row.get('reason'),missing=row.get('missing',[]),valuation=None)
        if row['status'] in SUCCESS:
            candidate['valuation']=summarize_run(row,run_directory)
        output['candidates'].append(candidate)
    candidates=output['candidates']
    if select is not None:
        preferred=[c for c in candidates if c['policy_version']==select]
        reason='explicit_policy_version'
    else:
        preferred=[c for c in candidates if c['kind']=='company_assignment']
        reason='configured_company_assignment_precedes_industry'
        if not preferred:
            # 未命中行业的配置不与真正命中的候选争夺默认结果。
            preferred=[c for c in candidates if c['status'] not in ('blocked_no_reviewed_industry_policy','blocked_policy_not_assigned')]
            reason='unique_industry_candidate'
    if len(preferred)>1:
        output.update(status='blocked_ambiguous_policy',reason='multiple equal-priority policies; supply --select')
    elif not preferred:
        output.update(status='blocked_policy_not_assigned',reason='no configured policy matches security')
    else:
        chosen=preferred[0]
        output.update(status=chosen['status'],valuation=chosen['valuation'])
        output['selection']=dict(policy_version=chosen['policy_version'],reason=reason,
                                 fallback_applied=False)
        if chosen['status'] not in SUCCESS:
            output['reason']=chosen['reason'] or chosen['status']
            output['missing']=chosen['missing']
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('code')
    parser.add_argument('--period',required=True)
    parser.add_argument('--as-of',required=True)
    parser.add_argument('--policy',action='append',required=True,help='BatchPolicy JSON，可重复指定，版本必须唯一')
    parser.add_argument('--select',help='显式选择某份policy_version；不会选择最高估值')
    parser.add_argument('--reference-database')
    parser.add_argument('--cash-check',action='store_true',help='并列核对第一年现金预测；另读上年同期标准窗口，缺项明确列示，不调整估值')
    parser.add_argument('--alphalake',default=str(Path(__file__).resolve().parents[3]/'alphalake'))
    args=parser.parse_args()
    try:
        if not re.fullmatch(r'\d{6}',args.code) or datetime.fromisoformat(args.as_of).utcoffset() is None:
            raise ValueError('six-digit code and timezone-aware ASOF required')
        policies=[load_policy(json.loads(Path(p).read_text()),args.reference_database,args.alphalake,args.as_of) for p in args.policy]
        def command(name,*extra,period=None):
            return json.loads(subprocess.check_output([args.alphalake,name,args.database,*extra,
                '--period',period or args.period,'--as-of',args.as_of],text=True,stderr=subprocess.PIPE,timeout=300))
        readiness=command('valuation-readiness')
        runs=os.environ.get('ALPHALAKE_VALUATION_RUN_DIR',str(Path(__file__).resolve().parents[1]/'data/alphalake_runs'))
        result=company_valuation(readiness,args.code,policies,lambda code:command('export-valuation',code),runs,args.select)
        if args.cash_check:
            result['cash_check']=dict(status='blocked_no_selected_valuation')
            if result.get('valuation'):
                from tools.compare_valuations import load_run,replay
                from data_sources.alphalake import AlphaLakeRequest
                from data_sources.alphalake_cash import cash_crosscheck
                run,receipt=load_run(runs,result['valuation']['run_id']);report,_=replay(run)
                request=AlphaLakeRequest.model_validate(run['request']);end=request.data.report_period
                previous=command('export-valuation',args.code,period=end.replace(year=end.year-1).isoformat())
                check=cash_crosscheck(request,previous,report)
                check['valuation_run_id']=run['run_id'];check['evidence']['valuation_run']=receipt
                check['check_id']=content_hash(check);result['cash_check']=check
        code=0 if result['status'] in SUCCESS else 1 if result['status']=='failed_execution' else 2
        payload=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
    except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError) as error:
        result=dict(contract_version=CONTRACT,code=args.code,report_period=args.period,information_as_of=args.as_of,
                    status='failed_request',error_type=type(error).__name__,reason=execution_error_reason(error),
                    selection=dict(policy_version=None,reason=None),valuation=None,candidates=[])
        code=1
        payload=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
    print(payload)
    raise SystemExit(code)


if __name__=='__main__':
    main()
