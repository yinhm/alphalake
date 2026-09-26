"""获准样本的估值输入代理；不修改SQLite财务列或标准事实。"""
import copy
from datetime import date
from decimal import Decimal
import json
import hashlib
import math
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parents[2] / 'policies' / 'tdx-book-assets-v1.json'


def apply(conn, record):
    if not record or not record.get('data_source'):
        return record
    policy_bytes = POLICY_PATH.read_bytes()
    policy = json.loads(policy_bytes)
    if record['company']['ticker'] not in policy['tickers']:
        return record
    result = copy.deepcopy(record)
    approval = dict(version=policy['version'], sha256=hashlib.sha256(policy_bytes).hexdigest(), status='estimated',
                    report_period=policy['report_period'], limitations=policy['limitations'], cells=[],
                    basis='initial_database_estimates; subsequent_user_overrides_are_separate',
                    source_snapshot={k:record['data_source'].get(k) for k in ('contract','report_period','information_as_of','source_database_sha256')})
    result['valuation_proxy'] = approval
    if record['data_source'].get('report_period') != policy['report_period']:
        approval.update(status='not_applicable', reason='report_period_not_approved')
        return result
    facts = {}
    for row in conn.execute('SELECT period,field,value,unit,period_type,statement_scope,evidence_json FROM standard_facts WHERE ticker=?', (record['company']['ticker'],)):
        fact = json.loads(row['evidence_json'])
        if any(fact.get(k) != row[k] for k in ('period','field','value','unit','period_type','statement_scope')) or fact.get('canonical_field') != row['field']:
            raise ValueError('inconsistent standard fact evidence')
        facts[(row['period'], row['field'])] = fact
    conflicts = {r[0] for r in conn.execute("SELECT DISTINCT period FROM export_cells WHERE ticker=? AND status='source_record_conflict'", (record['company']['ticker'],))}
    instrument = {r['instrument_id'] for r in facts.values()}
    if len(instrument) > 1:
        raise ValueError('proxy requires one unambiguous financial identity')
    instrument = next(iter(instrument), None)
    code = record['company']['ticker'].split(':')[-1]
    identities = [r[0] for r in conn.execute("SELECT instrument_id FROM export_universe WHERE code=? AND status='exported'", (code,))]
    if len(identities) != 1 or (instrument is not None and instrument != identities[0]) or any(r.get('code') != code for r in facts.values()):
        raise ValueError('proxy security identity differs from export universe')

    def calculate(terms, end, quarterly=False, partial=False):
        dates = [(end, 1)]
        if quarterly and end.month != 3:
            m = end.month - 3
            dates.append((date(end.year, m, (31 if m in (3, 12) else 30)), -1))
        missing, used, total = [], [], Decimal(0)
        for day, sign in dates:
            if day.isoformat() in conflicts:
                return None, 'source_record_conflict', [], []
            for field, weight in terms.items():
                r = facts.get((day.isoformat(), field))
                if r is None:
                    missing.append(dict(field=field, period=day.isoformat()))
                    continue
                expected = {3:'Q1', 6:'H1', 9:'9M', 12:'FY'}[day.month] if quarterly or field in policy['ebit_terms'] else 'instant'
                if (r['instrument_id'], r['unit'], r['period_type'], r['statement_scope'], r['source']) != (instrument,'CNY',expected,'provider_default','tdx'):
                    raise ValueError('invalid standard proxy component: '+field)
                value = Decimal(r['value'])
                if not value.is_finite() or (field not in policy['ebit_terms'] and value < 0):
                    raise ValueError('invalid proxy amount: '+field)
                total += value * weight * sign
                used.append(dict(field=field, period=day.isoformat(), coefficient=weight*sign, value=str(value), fact_id=r['fact_id'], artifact_sha256=r['artifact_sha256']))
        if not used or (missing and not partial):
            return None, 'missing_proxy_component', used, missing
        value = float(total / Decimal(1000000))
        if not math.isfinite(value):
            raise ValueError('proxy amount overflow')
        return value, 'estimated', used, missing

    for series, offset_key, period_key in [('annual','fy_offset','period_date_annual'),('quarterly','fq_offset','period_date_quarterly')]:
        base = date.fromisoformat(record['company'][period_key])
        for row in result['financials_'+series]:
            offset = row[offset_key]
            if series == 'annual':
                end = date(base.year-offset,12,31)
            else:
                y,q = divmod(base.year*4+base.month//3-1-offset,4)
                m=(q+1)*3
                end=date(y,m,31 if m in (3,12) else 30)
            recipes = {
                'cash_and_marketable_securities': ({f:1 for f in policy['cash_fields']},False),
                'cross_holdings': ({f:1 for f in policy['investment_fields']},True),
                'ebit': (policy['ebit_terms'],False),
            }
            for field,(terms,partial) in recipes.items():
                value,status,used,missing=calculate(terms,end,series=='quarterly' and field=='ebit',partial)
                original_value = row.get(field)
                row[field]=value
                approval['cells'].append(dict(series=series,offset=offset,period=end.isoformat(),field=field,value=value,
                    status=status,original_reported_value=original_value,formula=terms,source_components=used,omitted_components=missing,not_reported_fact=True))
            # 来源EBITDA不能与政策调整后的EBIT混成一套利润口径。
            row['ebitda'] = None
    return result
