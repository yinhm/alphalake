"""从本地主数据扫描、逐公司导出并调用既有估值入口；缺政策不猜、失败不中断其他公司。"""
import argparse
from collections import Counter
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from api.alphalake import evaluate, ENGINE_REVISION
from data_sources.alphalake import Policy, ScreenPolicy, BookDCFPolicy, HistoricalDCFPolicy, WACCBinding, AlphaLakeRequest, MissingInputs, content_hash


class Assignment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy: Policy | ScreenPolicy | BookDCFPolicy | HistoricalDCFPolicy
    wacc_binding: WACCBinding | None = None


class IndustryRule(BaseModel):
    model_config=ConfigDict(extra='forbid')
    rule_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    taxonomy_code: str = Field(min_length=1)
    node_codes: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)
    max_age_days: int = Field(ge=1,le=366)
    review_note: str = Field(min_length=1)
    policy: HistoricalDCFPolicy


class BatchPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy_version: str = Field(min_length=1)
    review_note: str = Field(min_length=1)
    assignments: dict[Annotated[str, Field(pattern=r'^\d{6}$')], Assignment]
    industry_rules: list[IndustryRule] = Field(default_factory=list)


def match_industry_rules(company, rules, cutoff):
    matches=[]
    for rule in rules:
        evidence=[]
        for member in company.get('industry_memberships') or []:
            if (member.get('source'),member.get('taxonomy_code'))!=(rule.source,rule.taxonomy_code) or member.get('node_code') not in rule.node_codes:
                continue
            observed=datetime.fromisoformat(member['observed_at'])
            finished=datetime.fromisoformat(member['run_finished_at'])
            if observed.utcoffset() is None or finished.utcoffset() is None or not cutoff-timedelta(days=rule.max_age_days)<=observed<=cutoff or finished>cutoff:
                continue
            evidence.append(member)
        if evidence:matches.append((rule,evidence))
    return matches


def run_batch(readiness, policy, export):
    """export 复用生产 Go 导出；每份证券快照独立事务，整批并非跨库原子快照。"""
    if readiness['contract_version'] != 'alphalake-readiness-v1':
        raise ValueError('unsupported readiness contract')
    results = []
    for company in readiness['companies']:
        row = {k:company[k] for k in ('instrument_id','name','symbols','financial_status','missing_core_fields')}
        if company.get('source_conflicts'):
            row.update(status='blocked_source_record_conflict',source_conflicts=company['source_conflicts'])
            results.append(row)
            continue
        symbols = company['symbols'] or []
        row['status'] = 'blocked_security_identity'
        if company['financial_status'] != 'blocked_security_identity' and len(symbols) == 1:
            code = symbols[0][2:]
            assignment = policy.assignments.get(code)
            row['code'] = code
            row['status'] = 'blocked_policy_not_assigned'
            if assignment is None and policy.industry_rules:
                try:
                    matches=match_industry_rules(company,policy.industry_rules,datetime.fromisoformat(readiness['information_as_of']))
                except (ValueError,KeyError,TypeError) as error:
                    row.update(status='rejected_industry_evidence',reason=str(error))
                    results.append(row)
                    continue
                row['status']='blocked_no_reviewed_industry_policy'
                if len(matches)>1:
                    row.update(status='blocked_ambiguous_industry_policy',matching_rules=[r.rule_id for r,_ in matches])
                elif matches:
                    rule,evidence=matches[0]
                    assignment=Assignment(policy=rule.policy)
                    row['policy_route']=dict(rule_id=rule.rule_id,classification_evidence=evidence)
            if assignment is not None:
                row.setdefault('policy_route',dict(kind='explicit_company_assignment'))
                try:
                    data = export(code)
                    if data['code'] != code or data['report_period'] != readiness['report_period'] or datetime.fromisoformat(data['information_as_of']) != datetime.fromisoformat(readiness['information_as_of']):
                        raise ValueError('export differs from requested security/period/cutoff')
                    # 代码复用不得使导出数据绕过扫描时的标准身份。
                    identities = {r['instrument_id'] for r in data['facts']+data['windows']}
                    if identities and identities != {company['instrument_id']}:
                        raise ValueError('export differs from scanned instrument identity')
                    request = AlphaLakeRequest(data=data,**assignment.model_dump())
                    result = evaluate(request)
                    row.update(status=result['status'],run_id=result['run_id'],
                        value_per_share=result['report']['final']['value_per_share'],
                        operating_enterprise_value_million_cny=result['report']['dcf']['value_of_operating_assets'])
                except MissingInputs as error:
                    row.update(status='blocked_missing_inputs',missing=error.items)
                except (ValueError,KeyError,TypeError,ArithmeticError) as error:
                    row.update(status='rejected_input_or_policy',reason=str(error))
                except (OSError,subprocess.SubprocessError) as error:
                    row.update(status='failed_execution',reason=str(error))
        results.append(row)
    if len(results) != readiness['universe_count']:
        raise ValueError('universe count does not match company rows')
    return dict(contract_version='alphalake-batch-v1',engine_revision=ENGINE_REVISION,
        readiness_sha256=content_hash(readiness),policy=policy.model_dump(mode='json'),
        report_period=readiness['report_period'],information_as_of=readiness['information_as_of'],
        universe_scope=readiness['universe_scope'],universe_count=len(results),
        status_counts=dict(sorted(Counter(r['status'] for r in results).items())),companies=results,
        boundary='conditional valuations; independent per-security snapshots; no implied market target')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('--period',required=True)
    parser.add_argument('--as-of',required=True)
    parser.add_argument('--policy',required=True,help='versioned assignments JSON; empty assignments allowed for census')
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--alphalake',default=str(Path(__file__).resolve().parents[3]/'alphalake'))
    args=parser.parse_args()
    policy=BatchPolicy.model_validate_json(Path(args.policy).read_text())
    def command(name,*extra):
        return json.loads(subprocess.check_output([args.alphalake,name,args.database,*extra,
            '--period',args.period,'--as-of',args.as_of],text=True,timeout=300))
    readiness=command('valuation-readiness')
    result=run_batch(readiness,policy,lambda code:command('export-valuation',code))
    # 每次尝试独立留档（包括失败）；成功单公司结果仍用既有内容寻址存储。
    root=Path(args.output_dir);root.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',prefix='batch-',suffix='.tmp',dir=root,delete=False) as f:
        temporary=Path(f.name)
        json.dump(result,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)
        f.write('\n');f.flush();os.fsync(f.fileno())
    target=temporary.with_suffix('.json');os.replace(temporary,target)
    print(json.dumps(dict(report=str(target),universe_count=result['universe_count'],status_counts=result['status_counts']),ensure_ascii=False))


if __name__=='__main__':
    main()
