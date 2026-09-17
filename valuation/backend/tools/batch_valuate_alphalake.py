"""从本地主数据扫描、逐公司导出并调用既有估值入口；缺政策不猜、失败不中断其他公司。"""
import argparse
from collections import Counter
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator
from api.alphalake import evaluate, ENGINE_REVISION
from data_sources.alphalake import Policy, ScreenPolicy, BookDCFPolicy, HistoricalDCFPolicy, CalibratedHistoricalDCFPolicy, ReviewedHistoricalDCFPolicy, WACCBinding, AlphaLakeRequest, MissingInputs, content_hash
from data_sources.alphalake_wacc import WACCPolicy, ReferenceSnapshot
from data_sources.alphalake_capital import CapitalBinding, CapitalPolicy, CapitalReferences


def execution_error_reason(error):
    """保留子进程原因；超时异常的stderr可能仍为bytes。"""
    stderr = getattr(error, 'stderr', None) or ''
    if isinstance(stderr, bytes):
        stderr = stderr.decode('utf-8', errors='replace')
    return str(error) + ('\nstderr: ' + stderr.strip() if stderr.strip() else '')


class Assignment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy: Policy | ScreenPolicy | BookDCFPolicy | HistoricalDCFPolicy | CalibratedHistoricalDCFPolicy | ReviewedHistoricalDCFPolicy
    wacc_binding: WACCBinding | None = None
    capital_binding: CapitalBinding | None = None


class IndustryWACCPolicy(WACCPolicy):
    # 行业模板不伪造公司身份，路由确认证券后才绑定具体代码。
    code: None = None


class IndustryCapitalPolicy(CapitalPolicy):
    code: None = None


class IndustryRule(BaseModel):
    model_config=ConfigDict(extra='forbid')
    rule_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    taxonomy_code: str = Field(min_length=1)
    node_codes: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)
    exchange_mic: Annotated[str, Field(pattern=r'^[A-Z0-9]{4}$')] | None = None
    node_names: dict[str, Annotated[str, Field(min_length=1)]] | None = None
    max_age_days: int = Field(ge=1,le=366)
    review_note: str = Field(min_length=1)
    policy: HistoricalDCFPolicy | CalibratedHistoricalDCFPolicy
    wacc_policy: IndustryWACCPolicy | None = None
    capital_policy: IndustryCapitalPolicy | None = None

    @model_validator(mode='after')
    def wacc_source(self):
        if self.node_names is not None and set(self.node_names)!=set(self.node_codes):
            raise ValueError('reviewed node names must cover exactly the rule codes')
        if (self.capital_policy is None)==(self.policy.sales_to_capital is None):
            raise ValueError('industry rule requires either direct capital ratio or reference policy')
        if self.capital_policy is not None and self.capital_policy.report_period!=self.policy.approved_report_period:
            raise ValueError('capital industry policy report period differs')
        if (self.wacc_policy is None) == (self.policy.wacc is None):
            raise ValueError('industry rule requires either fixed WACC or reference policy')
        if self.wacc_policy is not None and (self.wacc_policy.scope!='consolidated' or self.wacc_policy.market is not None or self.wacc_policy.report_period!=self.policy.approved_report_period):
            raise ValueError('industry WACC requires same-period consolidated target-weight policy')
        return self


class BatchPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy_version: str = Field(min_length=1)
    review_note: str = Field(min_length=1)
    assignments: dict[Annotated[str, Field(pattern=r'^\d{6}$')], Assignment]
    industry_rules: list[IndustryRule] = Field(default_factory=list)
    wacc_references: ReferenceSnapshot | None = None
    capital_references: CapitalReferences | None = None
    exclusions: dict[Annotated[str, Field(pattern=r'^\d{6}$')], Annotated[str, Field(min_length=1, pattern=r'\S')]] = Field(default_factory=dict)


def match_industry_rules(company, rules, cutoff):
    matches=[]
    for rule in rules:
        if rule.exchange_mic is not None and company.get('exchange_mic')!=rule.exchange_mic:
            continue
        evidence=[]
        for member in company.get('industry_memberships') or []:
            if (member.get('source'),member.get('taxonomy_code'))!=(rule.source,rule.taxonomy_code) or member.get('node_code') not in rule.node_codes:
                continue
            observed=datetime.fromisoformat(member['observed_at'])
            finished=datetime.fromisoformat(member['run_finished_at'])
            if observed.utcoffset() is None or finished.utcoffset() is None or not cutoff-timedelta(days=rule.max_age_days)<=observed<=cutoff or finished>cutoff:
                continue
            if rule.node_names is not None and member.get('node_name')!=rule.node_names[member['node_code']]:
                raise ValueError('industry name differs from reviewed policy mapping: '+member['node_code'])
            evidence.append(member)
        if evidence:matches.append((rule,evidence))
    return matches


def run_batch(readiness, policy, export, *, evaluator=evaluate):
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
            if code in policy.exclusions:
                row.update(status='blocked_review_exclusion',reason=policy.exclusions[code])
                results.append(row)
                continue
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
                    row['policy_route']=dict(rule_id=rule.rule_id,classification_evidence=evidence)
                    binding=None
                    if rule.wacc_policy is not None:
                        if policy.wacc_references is None:
                            row.update(status='blocked_missing_wacc_references')
                            results.append(row)
                            continue
                        try:
                            binding=WACCBinding(references=policy.wacc_references,policy=WACCPolicy.model_validate(
                                rule.wacc_policy.model_dump(exclude={'code'}) | {'code':code}))
                        except ValueError as error:
                            row.update(status='rejected_input_or_policy',reason=str(error))
                            results.append(row)
                            continue
                    capital_binding=None
                    if rule.capital_policy is not None:
                        if policy.capital_references is None:
                            row.update(status='blocked_missing_capital_references')
                            results.append(row)
                            continue
                        try:
                            capital_binding=CapitalBinding(references=policy.capital_references,policy=CapitalPolicy.model_validate(
                                rule.capital_policy.model_dump(exclude={'code'}) | {'code':code}))
                        except ValueError as error:
                            row.update(status='rejected_input_or_policy',reason=str(error))
                            results.append(row)
                            continue
                    assignment=Assignment(policy=rule.policy,wacc_binding=binding,capital_binding=capital_binding)
            if assignment is not None:
                row.setdefault('policy_route',dict(kind='explicit_company_assignment'))
                if isinstance(assignment.policy,BookDCFPolicy) and company['missing_core_fields']:
                    row.update(status='blocked_missing_inputs',missing=['TDX/'+f for f in company['missing_core_fields']])
                    results.append(row)
                    continue
                try:
                    data = export(code)
                    if data['code'] != code or data['report_period'] != readiness['report_period'] or datetime.fromisoformat(data['information_as_of']) != datetime.fromisoformat(readiness['information_as_of']):
                        raise ValueError('export differs from requested security/period/cutoff')
                    # 代码复用不得使导出数据绕过扫描时的标准身份。
                    identities = {r['instrument_id'] for r in data['facts']+data['windows']}
                    if identities and identities != {company['instrument_id']}:
                        raise ValueError('export differs from scanned instrument identity')
                    request = AlphaLakeRequest(data=data,**assignment.model_dump())
                    result = evaluator(request)
                    row.update(status=result['status'],run_id=result['run_id'],
                        value_per_share=result['report']['final']['value_per_share'],
                        operating_enterprise_value_million_cny=result['report']['dcf']['value_of_operating_assets'])
                except MissingInputs as error:
                    row.update(status='blocked_missing_inputs',missing=error.items)
                except (ValueError,KeyError,TypeError,ArithmeticError) as error:
                    row.update(status='rejected_input_or_policy',reason=str(error))
                except (OSError,subprocess.SubprocessError) as error:
                    row.update(status='failed_execution',reason=execution_error_reason(error))
        results.append(row)
    if len(results) != readiness['universe_count']:
        raise ValueError('universe count does not match company rows')
    return dict(contract_version='alphalake-batch-v1',engine_revision=ENGINE_REVISION,
        readiness_sha256=content_hash(readiness),company_industry_reference=readiness.get('company_industry_reference'),policy=policy.model_dump(mode='json'),
        report_period=readiness['report_period'],information_as_of=readiness['information_as_of'],
        universe_scope=readiness['universe_scope'],universe_count=len(results),
        status_counts=dict(sorted(Counter(r['status'] for r in results).items())),companies=results,
        boundary='conditional valuations; independent per-security snapshots; no implied market target')


def load_policy(raw_policy, reference_database, alphalake, as_of):
    """单公司与批次共用参考版本选择；不修改调用方传入的政策。"""
    raw_policy=dict(raw_policy)
    if reference_database:
        if raw_policy.get('wacc_references') is not None:
            raise ValueError('reference database and embedded reference packet are mutually exclusive')
        raw_policy['wacc_references']=json.loads(subprocess.check_output([alphalake,'export-wacc-references',
            reference_database,'--as-of',as_of,'--latest'],text=True,stderr=subprocess.PIPE,timeout=300))
    if reference_database and any(r.get('capital_policy') is not None for r in raw_policy.get('industry_rules',[])):
        if raw_policy.get('capital_references') is not None:
            raise ValueError('reference database and embedded capital packet are mutually exclusive')
        raw_policy['capital_references']=json.loads(subprocess.check_output([alphalake,'export-industry-capital',
            reference_database,'--as-of',as_of],text=True,stderr=subprocess.PIPE,timeout=300))
    return BatchPolicy.model_validate(raw_policy)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('--query-timeout',type=int,default=300,help='单次标准查询秒数；刷新入口受整个阶段超时约束')
    parser.add_argument('--period',required=True)
    parser.add_argument('--as-of',required=True)
    parser.add_argument('--policy',required=True,help='versioned assignments JSON; empty assignments allowed for census')
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--previous-report',help='前一批次JSON，核验后复用未变化运行；失败保留最后成功记录')
    parser.add_argument('--alphalake',default=str(Path(__file__).resolve().parents[3]/'alphalake'))
    parser.add_argument('--reference-database',help='按同一信息时点自动选择此库四类WACC参考版本')
    args=parser.parse_args()
    if args.query_timeout < 1:
        parser.error('positive query timeout required')
    try:
        policy=load_policy(json.loads(Path(args.policy).read_text()),args.reference_database,args.alphalake,args.as_of)
    except ValueError as error:
        parser.error(str(error))
    def command(name,*extra):
        return json.loads(subprocess.check_output([args.alphalake,name,args.database,*extra,
            '--period',args.period,'--as-of',args.as_of],text=True,stderr=subprocess.PIPE,timeout=args.query_timeout))
    readiness=command('valuation-readiness')
    from tools.incremental_valuation import run_incremental_batch
    previous=json.loads(Path(args.previous_report).read_text()) if args.previous_report else None
    if previous and previous.get('source_database') != str(Path(args.database).resolve()):
        raise ValueError('previous report belongs to a different database')
    runs=os.environ.get('ALPHALAKE_VALUATION_RUN_DIR',str(Path(__file__).resolve().parents[1]/'data/alphalake_runs'))
    result=run_incremental_batch(readiness,policy,lambda code:command('export-valuation',code),previous,runs)
    result['source_database']=str(Path(args.database).resolve())
    # 每次尝试独立留档（包括失败）；成功单公司结果仍用既有内容寻址存储。
    root=Path(args.output_dir);root.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',prefix='batch-',suffix='.tmp',dir=root,delete=False) as f:
        temporary=Path(f.name)
        json.dump(result,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)
        f.write('\n');f.flush();os.fsync(f.fileno())
    target=temporary.with_suffix('.json');os.replace(temporary,target)
    print(json.dumps(dict(report=str(target),universe_count=result['universe_count'],status_counts=result['status_counts']),ensure_ascii=False))


if __name__=='__main__':
    try:
        main()
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        print(execution_error_reason(error), file=sys.stderr)
        raise SystemExit(1)
