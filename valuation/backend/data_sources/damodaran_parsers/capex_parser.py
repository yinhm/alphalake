"""Parse Damodaran capex spreadsheets (capex.xls, capexGlobal.xls, etc.).

Key field: Sales/Invested Capital (LTM) → sales_to_capital
"""
from __future__ import annotations

from pathlib import Path

if __package__:
    from .xls_utils import open_data_sheet, find_header_row, get_headers, build_col_map, safe_float, safe_str
else:
    from xls_utils import open_data_sheet, find_header_row, get_headers, build_col_map, safe_float, safe_str


def parse_capex(file_path: str | Path) -> dict[str, dict]:
    """Parse capex .xls → dict keyed by industry name."""
    ws = open_data_sheet(str(file_path), "Industry Averages")
    header_row = find_header_row(ws, "Industry Name")
    col = build_col_map(get_headers(ws, header_row))

    result: dict[str, dict] = {}
    for r in range(header_row + 1, ws.nrows):
        name = (safe_str(ws, r, col.get("Industry Name")) or "").strip()
        if not name or name.lower() in ("total market", "total"):
            continue
        result[name] = {
            "sales_to_capital": safe_float(ws, r, col.get("Sales/ Invested Capital (LTM)")),
            "capex_to_depreciation": safe_float(ws, r, col.get("Cap Ex/Deprecn")),
            "net_capex_to_sales": safe_float(ws, r, col.get("Net Cap Ex/Sales")),
        }
    return result


def alphalake_capital_snapshot(file_path: str | Path) -> dict:
    """全球行业收入/投入资本参考；不将行业平均视作公司预测事实。"""
    import hashlib
    import math
    import platform
    from decimal import Decimal, ROUND_HALF_EVEN
    import xlrd

    path = Path(file_path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('oversized capital workbook')
    wb = xlrd.open_workbook(path)
    ws = wb.sheet_by_name('Industry Averages')
    expected = ['Industry Name', 'Number of Firms', 'Capital Expenditures (US $ millions)',
                'Depreciation & Amort ((US $ millions)', 'Cap Ex/Deprecn', 'Acquisitions (US $ millions)',
                'Net R&D (US $ millions)', 'Net Cap Ex/Sales', 'Net Cap Ex/ EBIT (1-t)',
                'Sales/ Invested Capital (LTM)']
    if ws.nrows != 104 or ws.ncols != 10 or ws.row_values(7) != expected or ws.cell_value(2, 5) != 'Global':
        raise ValueError('unsupported global capital scope/headers')
    if ws.cell_value(102, 0) != 'Total Market' or ws.cell_value(103, 0) != 'Total Market (without financials)':
        raise ValueError('missing capital totals')
    if ws.cell_value(0, 0) != 'Date updated:' or ws.cell_type(0, 1) != xlrd.XL_CELL_DATE:
        raise ValueError('missing capital observation date')
    parsed = parse_capex(path)
    rows, names = [], set()
    for r in range(8, 102):
        name, n, v = ws.cell_value(r, 0), ws.cell_value(r, 1), ws.cell_value(r, 9)
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError('missing/duplicate capital industry')
        names.add(name)
        if ws.cell_type(r, 1) != xlrd.XL_CELL_NUMBER or not math.isfinite(n) or n <= 0 or n != int(n):
            raise ValueError('invalid capital sample count')
        if ws.cell_type(r, 9) != xlrd.XL_CELL_NUMBER or not math.isfinite(v) or v <= 0:
            raise ValueError('invalid sales/invested capital ratio')
        if parsed[name]['sales_to_capital'] != v:
            raise ValueError('capital parser disagreement')
        rows.append(dict(industry=name, sample_count=int(n), metric_code='sales_to_invested_capital_ltm',
                         source_locator=f'Industry Averages!J{r+1}', raw_value=str(v),
                         value=str(Decimal(str(v)).quantize(Decimal('0.000000000001'), rounding=ROUND_HALF_EVEN))))
    date = xlrd.xldate_as_datetime(ws.cell_value(0, 1), wb.datemode).date().isoformat()
    wb.release_resources()
    return dict(contract='alphalake-global-capital-v1', observation_date=date,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(), parser_version='damodaran-global-capital-v1',
                runtime=f'python={platform.python_version()};xlrd={xlrd.__version__}', observations=rows)


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser(description='AlphaLake global industry capital snapshot')
    parser.add_argument('workbook', type=Path)
    print(json.dumps(alphalake_capital_snapshot(parser.parse_args().workbook), sort_keys=True, allow_nan=False))
