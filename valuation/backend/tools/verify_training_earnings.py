"""两家训练样本的利润主表与附注范围；不覆盖TDX源值。"""
from decimal import Decimal
import hashlib
import json
import re
import struct
import subprocess
from statistics import median

from tools import backtest_tdx_zero_calibration as joint
from tools import diagnose_tdx_training_exclusions as exclusions
from tools.backtest_tdx_history import EBIT, value

DIRECTORY = exclusions.DIRECTORY / 'earnings-review'
LABELS = dict(FN86='三、营业利润', FN305='其中：利息费用', FN306='利息收入',
              FN83='投资收益（', FN82='公允价值变动收益', FN301='资产处置收益')


def statement(text, unit, period, labels=LABELS):
    body = text.split('合并利润表', 1)[1].split('母公司利润表', 1)[0]
    clean = lambda s: re.sub(r'\s+', '', s)
    expected_unit = '单位：千元' if unit == 1000 else '单位：元'
    expected_period = period[:4] + ('年1—6月' if period.endswith('06-30') else '年1—12月')
    if (unit not in (1, 1000) or expected_unit not in clean(body[:450]).replace(':', '：')
            or expected_period not in clean(body[:450])):
        raise ValueError('consolidated statement unit/period differs')
    money = re.compile(r'-?\d[\d,]*' if unit == 1000 else r'-?\d[\d,]*\.\d{2}')
    lines = []; boundaries = []
    for page in body.split('\f'):
        page_lines = page.splitlines()
        pairs = [list(money.finditer(l)) for l in page_lines]
        gaps = [(m[0].end()+m[1].start())/2 for m in pairs if len(m)==2 and m[0].start()>10]
        split = median(gaps) if gaps else None
        lines.extend(page_lines); boundaries.extend([split]*len(page_lines))
    interest_line = next(i for i, l in enumerate(lines) if clean(l).startswith(LABELS['FN305']))
    results = {}
    for field, prefix in labels.items():
        found = [i for i, l in enumerate(lines) if clean(l).startswith(prefix)
                 and (field != 'FN306' or i > interest_line)]
        if len(found) != 1:
            raise ValueError('statement field identity differs: ' + field)
        i = found[0]; block = [lines[i]]
        # Fixed reports wrap labels; continuation must not absorb the next item.
        for following in lines[i+1:i+3]:
            stripped = following.strip()
            if stripped and (stripped[0].isdigit() or stripped[0] in '-“号列失填七'):
                block.append(following)
            else:
                break
        numbers = []
        for offset, line in enumerate(block):
            found = list(money.finditer(line))
            if len(found) == 2:
                numbers.append(found[0])
            elif len(found) == 1:
                split = boundaries[i+offset]
                if split is None:
                    raise ValueError('missing current column boundary')
                if found[0].end() <= split:
                    numbers.append(found[0])
            elif len(found) > 2:
                raise ValueError('unexpected statement columns')
        if len(numbers) > 1:
            raise ValueError('ambiguous current column: ' + field)
        amount = str(Decimal(numbers[0].group().replace(',', '')) * unit) if numbers else None
        results[field] = dict(current_cny=amount, lines=block)
    return results


def verify(ledger=None):
    if ledger is None:
        ledger = json.loads((DIRECTORY/'ledger.json').read_bytes())
    expected = [('600751', p) for p in ('2021-06-30','2021-12-31','2022-06-30','2022-12-31','2023-06-30')]
    expected += [('688670', p) for p in ('2022-06-30','2022-12-31','2023-06-30','2023-12-31','2024-06-30')]
    if [(r['code'],r['period']) for r in ledger] != expected:
        raise ValueError('report identity set differs')
    _, _, source = joint.load_inputs(exclusions.DIRECTORY/'protocol.json')
    index = {}
    for r in source['records']:
        index.setdefault((r['code'],r['period']), []).append(r)
    comparisons = []; notes = {}; blanks = set()
    for report in ledger:
        code, period = report['code'], report['period']; path = DIRECTORY/report['file']
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != report['sha256'] or len(raw) != report['size']:
            raise ValueError('PDF bytes differ')
        text = subprocess.check_output(['pdftotext','-layout',str(path),'-'],text=True)
        parsed = statement(text, report['unit_multiplier'], period)
        if parsed != report['statement']:
            raise ValueError('statement extraction differs')
        records = index[code,period]
        if len(records) != 1:
            raise ValueError('source identity differs')
        source_row = records[0]
        for field, item in parsed.items():
            amount = item['current_cny']; bits = source_row['bits'][field]
            if amount is None:
                blanks.add((code,period,field))
                if value(source_row,field) != 0:
                    raise ValueError('blank statement has nonzero source')
                status = 'blank_statement_source_zero_not_proven_economic_zero'
            else:
                if struct.unpack('<I',struct.pack('<f',float(Decimal(amount))))[0] != bits:
                    raise ValueError('source bits differ')
                status = 'matched'
            comparisons.append(dict(code=code,period=period,field=field,pdf_cny=amount,
                source_bits=bits,source_cny=str(value(source_row,field)),status=status))
        if code != '600751':
            continue
        credit = statement(text,1000,period,{'credit':'信用减值损失'})['credit']
        if credit != report['credit_statement']:
            raise ValueError('credit statement differs')
        pages = text.split('\f')
        verified = {}
        for key, row in report['notes'].items():
            if row['line'] not in pages[row['page']-1].splitlines():
                raise ValueError('note line differs')
            numbers = [n.replace(',','') for n in re.findall(r'-?\d[\d,]*',row['line'])]
            if numbers != row['printed_thousands']:
                raise ValueError('note columns differ')
            verified[key] = Decimal(numbers[0])*1000
        sign = Decimal(credit['current_cny']) / verified['credit_total']
        if sign not in (Decimal(-1),Decimal(1)):
            raise ValueError('credit note sign does not reconcile to statement')
        verified['credit_note_sign_to_profit'] = sign
        verified['guarantee_profit_contribution'] = verified['guarantee']*sign
        # These five reports present deducted income with two different signs.
        income_sign = -1 if period in ('2021-06-30','2021-12-31','2022-12-31') else 1
        verified['positive_interest_income'] = verified['interest_income']*income_sign
        notes[period] = verified
    annual = notes['2021-12-31']
    comparative = next(r for r in ledger if (r['code'],r['period'])==('600751','2022-12-31'))
    if annual['borrow_interest']+annual['lease_interest'] != Decimal(comparative['notes']['total_interest']['printed_thousands'][1])*1000:
        raise ValueError('annual interest scope bridge differs')
    result = exclusions.load_result(); windows = []
    for code, origin in (('600751','2023-06-30'),('688670','2024-06-30')):
        training = next(r for r in result['training'][origin]['admitted'] if r['code']==code)
        for side, current in (('prior',training['prior']['current']),('realized',training['realized'])):
            parts = {}
            for field in LABELS:
                refs = [r for r in current['source_inputs'] if r['field']==field]
                parts[field] = sum(value(index[code,r['period']][0],field)*r['coefficient'] for r in refs)
                if float(parts[field]) != current['ebit_components_cny'][field]:
                    raise ValueError('stored TTM component differs')
            source_ebit = sum(parts[f]*sign for f,sign in EBIT.items())
            refs = [r for r in current['source_inputs'] if r['field']=='FN86']
            end = refs[0]['period']
            window = dict(code=code,end=end,source_ebit_proxy_cny=str(source_ebit),
                blank_statement_components=[dict(period=r['period'],field=r['field']) for r in current['source_inputs']
                    if (code,r['period'],r['field']) in blanks], full_normalized_ebit_cny=None)
            if code == '600751':
                guarantee = sum(notes[r['period']]['guarantee_profit_contribution']*r['coefficient'] for r in refs)
                delta = Decimal(0)
                for r in refs:
                    period = r['period']
                    for field,key,sign in (('FN305','borrow_interest',1),('FN306','positive_interest_income',-1)):
                        if (code,period,field) in blanks:
                            delta += notes[period][key]*r['coefficient']*sign
                window.update(guarantee_profit_contribution_cny=str(guarantee),
                    source_proxy_less_guarantee_cny=str(source_ebit-guarantee),
                    mechanical_missing_interest_delta_cny=str(delta),
                    mechanical_interest_and_guarantee_sensitivity_cny=str(source_ebit+delta-guarantee))
            windows.append(window)
    exposure = {}
    for origin,pool in result['training'].items():
        codes = []
        for entry in pool['admitted']:
            refs = entry['prior']['current']['source_inputs'] + entry['realized']['source_inputs']
            if any(r['field'] in ('FN305','FN306') and value(index[entry['code'],r['period']][0],r['field']) == 0 for r in refs):
                codes.append(entry['code'])
        exposure[origin] = dict(training_count=len(pool['admitted']),any_interest_source_zero_codes=codes)
    return dict(statement_comparisons=comparisons, hna_notes={p:{k:str(v) for k,v in n.items()} for p,n in notes.items()},
        ttm_windows=windows, training_interest_zero_exposure=exposure,
        hna_2022H1_borrow_interest_note_ttm_cny=str(notes['2022-06-30']['borrow_interest']+annual['borrow_interest']-notes['2021-06-30']['borrow_interest']),
        boundary='source_proxy_replay_not_economic_EBIT_validation; source_zero_can_hide_note_values; '
        'interest_scope_and_cross_period_comparability_unresolved; mechanical_sensitivity_not_normalized_profit; '
        'training_zero_exposure_not_confirmed_missing_data_count; no_source_or_production_change')


if __name__ == '__main__':
    print(json.dumps(verify(),ensure_ascii=False,indent=2))
