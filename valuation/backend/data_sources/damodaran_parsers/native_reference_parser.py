"""原生模型参考入库：按原表表头取值，保留单位、区域、缺项与单元格。"""
from pathlib import Path
import hashlib
import json
import math
import platform
from decimal import Decimal, ROUND_HALF_EVEN
import xlrd
if __package__:
    from .xls_utils import find_header_row, get_headers
else:
    from xls_utils import find_header_row, get_headers

# metric, exact header, occurrence (repeated headers have distinct meanings).
FIELDS = {
    'beta': [('beta_unlevered', 'Unlevered beta', 0), ('beta_unlevered_cash_adjusted', 'Unlevered beta corrected for cash', 0), ('debt_equity_ratio', 'D/E Ratio', 0), ('effective_tax_rate', 'Effective Tax rate', 0)],
    'wacc': [('cost_of_equity', 'Cost of Equity', 0), ('cost_of_debt_pretax', 'Cost of Debt', 0), ('weighted_average_cost_of_capital', 'Cost of Capital', 0), ('equity_return_standard_deviation', 'Std Dev in Stock', 0)],
    'margin': [('pretax_operating_margin', 'Pre-tax Unadjusted Operating Margin', 0), ('aftertax_operating_margin', 'After-tax Unadjusted Operating Margin', 0), ('pretax_lease_research_adjusted_operating_margin', 'Pre-tax Lease & R&D adj Margin', 0), ('aftertax_lease_research_adjusted_operating_margin', 'After-tax Lease & R&D adj Margin', 0)],
    'taxrate': [('profitable_firms_effective_tax_rate', 'Average across only money-making companies', 0)],
    'capex': [('sales_to_invested_capital_ltm', 'Sales/ Invested Capital (LTM)', 0)],
    'fundgrEB': [('expected_ebit_growth', 'Expected Growth in EBIT', 0)],
    'EVA': [('return_on_invested_capital', 'ROC', 0), ('debt_capital_ratio', 'D/(D+E)', 0)],
    'vebitda': [('enterprise_value_ebitda_multiple', 'EV/EBITDA', 1)],
    'pe': [('current_price_earnings_multiple', 'Current PE', 0)],
    'pbv': [('price_book_multiple', 'PBV', 0)],
    'ps': [('enterprise_value_sales_multiple', 'EV/Sales', 0)],
    'countrytaxrates': [('corporate_marginal_tax_rate', 'Corporate Tax Rate', 0)],
}
US_NAMES = {'betas': 'beta', 'pedata': 'pe', 'pbvdata': 'pbv', 'psdata': 'ps'}
COUNTRIES = {'China': 'CN', 'China, Hong Kong Special Administrative Region': 'HK', 'United States of America': 'US', 'Israel': 'IL'}


def snapshot(path):
    path = Path(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('oversized reference workbook')
    stem = path.stem.split('-')[0]  # immutable archive suffix, not a different dataset
    region = 'global' if stem.endswith('Global') else 'us'
    family = US_NAMES.get(stem, stem.removesuffix('Global'))
    if family not in FIELDS:
        raise ValueError('unreviewed native reference file: '+stem)
    tax = family == 'countrytaxrates'
    wb = xlrd.open_workbook(path)
    try:
        ws = wb.sheet_by_name('Sheet1' if tax else 'Industry Averages')
        label = 'Country' if tax else 'Industry name' if family == 'taxrate' else 'Industry Name'
        row = find_header_row(ws, label)
        headers = get_headers(ws, row)
        if ws.cell_value(0, 0) != 'Date updated:' or ws.cell_type(0, 1) != xlrd.XL_CELL_DATE:
            raise ValueError('missing reference observation date')
        if not tax and ws.cell_value(2, 5) != ('Global' if region == 'global' else 'US companies'):
            raise ValueError('reference sample region mismatch')
        unlevering_tax_rate=''
        if family=='beta':
            tax_choice=ws.cell_value(8,5)
            if ws.cell_value(7,5)!='Marginal' or not isinstance(tax_choice,(int,float)) or not 0<=tax_choice<=1:
                raise ValueError('unsupported beta unlevering tax choice')
            unlevering_tax_rate=str(Decimal(str(tax_choice)).quantize(Decimal('0.000000000001')))
        columns = []
        for metric, header, occurrence in FIELDS[family]:
            matches = [i for i, h in enumerate(headers) if h == header]
            if len(matches) <= occurrence:
                raise ValueError('missing required header: '+header)
            columns.append((metric, matches[occurrence]))
        observations, seen = [], set()
        for r in range(row+1, ws.nrows):
            name = str(ws.cell_value(r, 0)).strip()
            if not name or name.startswith('Total Market') or name == 'Total':
                continue
            if tax and name not in COUNTRIES:
                continue
            if name in seen:
                if not tax:
                    raise ValueError('duplicate reference subject: '+name)
                previous=next(o for o in observations if o['subject']==COUNTRIES[name])
                previous['value']=None
                previous['value_status']='ambiguous'
                previous['raw_value'] += ';'+str(ws.cell_value(r,columns[0][1]))
                previous['source_locator'] += f';{ws.name}!{xlrd.formula.colname(columns[0][1])}{r+1}'
                continue
            seen.add(name)
            sample = None if tax else ws.cell_value(r, 1)
            if not tax and (not isinstance(sample, (float, int)) or sample < 0 or sample != int(sample)):
                raise ValueError('invalid sample count')
            if family=='beta':
                levered,de,unlevered,cash,corrected=[ws.cell_value(r,c) for c in (2,3,5,6,7)]
                if abs(unlevered-levered/(1+(1-tax_choice)*de))>1e-10 or abs(corrected-unlevered/(1-cash))>1e-10:
                    raise ValueError('beta source formula mismatch')
            for metric, col in columns:
                raw = ws.cell_value(r, col)
                number = ws.cell_type(r, col) == xlrd.XL_CELL_NUMBER
                if number and not math.isfinite(raw):
                    raise ValueError('nonfinite reference')
                if tax and (not number or not 0 <= raw <= 1):
                    raise ValueError('missing/invalid corporate tax rate')
                value = format(Decimal(str(raw)).quantize(Decimal('0.000000000001'), rounding=ROUND_HALF_EVEN), 'f') if number else None
                unit = 'dimensionless' if 'beta_' in metric or 'multiple' in metric or metric == 'sales_to_invested_capital_ltm' else 'fraction'
                observations.append(dict(subject=COUNTRIES[name] if tax else name, metric_code=metric,
                    source_locator=f'{ws.name}!{xlrd.formula.colname(col)}{r+1}', raw_value=str(raw), raw_unit=unit,
                    sample_count=None if tax else int(sample), value=value, value_status='reported' if number else 'missing'))
        if len(seen) != (len(COUNTRIES) if tax else 94):
            raise ValueError(f'incomplete reference scope: {stem}: {len(seen)}')
        return dict(contract='alphalake-native-reference-source-v1', parser_version='damodaran-native-reference-v3',
            observation_date=xlrd.xldate_as_datetime(ws.cell_value(0, 1), wb.datemode).date().isoformat(),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), runtime=f'python={platform.python_version()};xlrd={xlrd.__version__}',
            unlevering_tax_rate=unlevering_tax_rate, file_stem=stem, sample_region='' if tax else region, observations=observations)
    finally:
        wb.release_resources()

if __name__ == '__main__':
    import sys
    print(json.dumps(snapshot(sys.argv[1]), allow_nan=False, sort_keys=True))
