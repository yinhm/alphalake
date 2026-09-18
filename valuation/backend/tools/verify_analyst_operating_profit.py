"""同六份既定研报：营业利润与收入配对诊断，不替代调整EBIT。"""
import argparse
from collections import Counter
from datetime import date
from decimal import Decimal as D, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re

from pypdf import PdfReader
from tools.backtest_tdx_history import available, at, window
from tools.tdx_research_source import financial_value


def build(directory, pdf_directory, history_source=None):
    plan = json.loads((directory/'operating-profit-plan.json').read_bytes())
    inputs = {}
    for name, digest in plan['inputs'].items():
        raw = Path(name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('frozen input hash differs: '+name)
        inputs[Path(name).name] = json.loads(raw)
    source = inputs['capital-snapshot.json']
    records = {(r['code'],r['period']):r for r in source['records']}
    if len(records) != len(source['records']):
        raise ValueError('duplicate source identity')
    artifacts = {a['file']:a for a in source['artifacts']}
    supplement_hash = None
    if history_source is not None:
        raw = history_source.read_bytes()
        supplement_hash = hashlib.sha256(raw).hexdigest()
        if supplement_hash != '9b8d66382f58f01ee798a7735708a945e80485f1c1d810a1dc43631e00714321':
            raise ValueError('supplement source hash differs')
        supplement = json.loads(raw)
        completion = (directory/'operating-profit-completion-plan.json').read_bytes()
        if supplement['study_sha256'] != hashlib.sha256(completion).hexdigest():
            raise ValueError('completion plan hash differs')
        for row in supplement['records']:
            key = (row['code'], row['period'])
            if key != ('300866', '2020-12-31') or key in records:
                raise ValueError('supplement cannot replace source history')
            records[key] = row
        for artifact in supplement['artifacts']:
            if artifact['file'] in artifacts:
                raise ValueError('supplement cannot replace artifact')
            artifacts[artifact['file']] = artifact
    specs = json.loads((directory/'operating-profit-evidence.json').read_bytes())
    reports = inputs['sources.json']
    if [s['info_code'] for s in specs] != [s['info_code'] for s in reports]:
        raise ValueError('report selection differs')
    normalized = lambda s:re.sub(r'\s+', '', s).replace('（','(').replace('）',')')
    forecasts, history = {}, []
    for spec, report in zip(specs, reports):
        raw = (pdf_directory/report['pdf_file']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != report['metadata']['sha256']:
            raise ValueError('PDF hash differs')
        text = PdfReader(pdf_directory/report['pdf_file']).pages[spec['page']-1].extract_text()
        if any(normalized(s) not in normalized(text) for s in (spec['section'],spec['unit'],' '.join(spec['years']),spec['row'])):
            raise ValueError('PDF header/unit/row differs')
        number = r'-?[\d,]+(?:\.\d+)?'
        matches = re.findall(r'营业利润\s+('+number+r'(?:\s+'+number+r'){'+str(len(spec['years'])-1)+r'})(?=\s|$)',text)
        if len(matches) != 1:
            raise ValueError('operating profit row ambiguous')
        amounts = [D(s.replace(',','')) for s in matches[0].split()]
        issues = []
        for label, amount in zip(spec['years'],amounts):
            year = int(label[:4])
            if label.endswith('E'):
                forecasts[report['info_code'],year] = amount*1000000
                continue
            record = records.get((report['code'],f'{year}-12-31'))
            status = 'missing_source_history'
            if record is not None:
                if available(record,artifacts[record['artifact']]) > at(str(int(report['provider_date'][:4]))+'-09-01T00:00:00+08:00'):
                    status = 'unavailable_source_history'
                else:
                    encoded = financial_value(record,'operating_profit_cumulative')/1000000
                    status = 'matched' if encoded.quantize(D(1),rounding=ROUND_HALF_UP)==amount else 'history_amount_differs'
            history.append(dict(info_code=report['info_code'],year=year,printed_million_cny=str(amount),status=status))
            if status != 'matched':issues.append(status+':'+str(year))
        spec['issues'] = issues
    by_report = {s['info_code']:s for s in specs}
    output = []
    for original in inputs['comparison.json']['results']:
        r = {k:original[k] for k in ('code','origin_year','target_year','info_code','status')}
        r['broker_operating_profit_cny'] = str(forecasts[r['info_code'],r['target_year']])
        r['historical_anchor_issues'] = by_report[r['info_code']]['issues']
        r['revenue_predictions_cny'] = original['predictions_cny']
        end = date(r['origin_year'],6,30)
        known = {period:record for (code,period),record in records.items() if code==r['code'] and available(record,artifacts[record['artifact']])<=at(original['forecast_as_of'])}
        margin = window(known,end,'operating_profit_cumulative')[0]/window(known,end,'revenue')[0]
        predicted = dict(broker=D(r['broker_operating_profit_cny']),companion_benchmark=D(original['predictions_cny']['current_rule_fy_bridge'])*margin)
        r.update(origin_reported_margin=str(margin),profit_predictions_cny={k:str(v) for k,v in predicted.items()})
        if r['historical_anchor_issues']:
            r['status'] = 'blocked_history_anchor'
        elif r['status'] == 'evaluated':
            actual = records[r['code'],str(r['target_year'])+'-12-31']
            if available(actual,artifacts[actual['artifact']]) > at('2026-09-10T00:00:00+08:00'):
                raise ValueError('actual not available at fixed cutoff')
            profit = financial_value(actual,'operating_profit_cumulative'); revenue = D(original['actual_revenue_cny'])
            r.update(actual_profit_cny=str(profit),actual_revenue_cny=str(revenue),origin_reported_margin=str(margin),actual_bits=actual['bits']['FN86'],profit_predictions_cny={k:str(v) for k,v in predicted.items()},profit_errors_cny={k:str(v-profit) for k,v in predicted.items()},paired_revenue_errors_pct={k:original['errors_pct_actual'][k] for k in ('broker','current_rule_fy_bridge')})
        output.append(r)
    if len(output) != plan['positions']:
        raise ValueError('position count differs')
    def metrics(rows):
        valid = [r for r in rows if r['status']=='evaluated']
        result = dict(positions=len(rows),statuses=dict(Counter(r['status'] for r in rows)),models={})
        for model, rev in [('broker','broker'),('companion_benchmark','current_rule_fy_bridge')]:
            result['models'][model] = dict(n=len(valid),profit_mae_pct_revenue=sum(float(abs(D(r['profit_errors_cny'][model]))/D(r['actual_revenue_cny'])*100) for r in valid)/len(valid) if valid else None,profit_wape_pct=float(sum(abs(D(r['profit_errors_cny'][model])) for r in valid)/sum(abs(D(r['actual_profit_cny'])) for r in valid)*100) if valid and sum(abs(D(r['actual_profit_cny'])) for r in valid) else None,revenue_mae_pct=sum(abs(r['paired_revenue_errors_pct'][rev]) for r in valid)/len(valid) if valid else None)
        return result
    result = dict(plan_sha256=hashlib.sha256((directory/'operating-profit-plan.json').read_bytes()).hexdigest(),evidence_sha256=hashlib.sha256((directory/'operating-profit-evidence.json').read_bytes()).hexdigest(),history=history,results=output,summary=metrics(output),by_company={c:metrics([r for r in output if r['code']==c]) for c in plan['codes']},by_origin={str(y):metrics([r for r in output if r['origin_year']==y]) for y in plan['origins']},decision=plan['decision'],boundary=plan['boundary'])

    if supplement_hash is not None:
        result['history_source_sha256'] = supplement_hash
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('pdf_directory',type=Path)
    parser.add_argument('--history-source',type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.directory,args.pdf_directory,args.history_source),ensure_ascii=False,indent=2))
