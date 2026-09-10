"""解析官方公司行业名单；来源未标日期，不推断发布日期或分类生效日。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import re

import xlrd

HEADERS = ['Company Name', 'Exchange:Ticker', 'Industry Group', 'Primary Sector',
           'SIC Code', 'Country', 'Broad Group', 'Sub Group']
SHEETS = ['By industry', 'By company name', 'By geography']


def alphalake_company_industry_snapshot(file_path: str | Path) -> dict:
    path = Path(file_path)
    if not 0 < path.stat().st_size <= 64 * 1024 * 1024:
        raise ValueError('empty/oversized company workbook')
    book = xlrd.open_workbook(path, on_demand=True)
    try:
        if book.sheet_names() != SHEETS:
            raise ValueError('unsupported company workbook sheets')
        reference = None
        companies = []
        for name in SHEETS:
            sheet = book.sheet_by_name(name)
            if sheet.ncols != 8 or sheet.row_values(0) != HEADERS or sheet.nrows != 48157:
                raise ValueError('unsupported company headers/audited scope')
            rows = [tuple(sheet.row_values(r)) for r in range(1, sheet.nrows)]
            current = Counter(rows)
            if reference is not None and current != reference:
                raise ValueError('company sheets disagree')
            reference = current
            if name == 'By company name':
                industries = sorted({r[2] for r in rows})
                tickers = [r[1] for r in rows if r[1]]
                unidentified = len(rows) - len(tickers)
                if len(industries) != 94 or len(set(tickers)) != len(tickers) or unidentified != 12:
                    raise ValueError('unsupported/ambiguous company identities')
                for number, row in enumerate(rows, 2):
                    if not str(row[1]).startswith(('SHSE:', 'SZSE:')):
                        continue
                    if (not all(isinstance(row[i], str) for i in (0, 1, 2, 3, 5, 6, 7))
                            or not (isinstance(row[4], str) or type(row[4]) in (int, float) and row[4] == 0)
                            or not re.fullmatch(r'(SHSE|SZSE):[0-9]{6}', row[1])
                            or not all(row[i].strip() for i in (0, 2, 3, 5))):
                        raise ValueError(f'invalid company row {number}')
                    companies.append(dict(zip(
                        ['name', 'ticker', 'industry', 'sector', 'sic_code', 'country', 'broad_group', 'sub_group'], row),
                        source_locator=f'{name}!A{number}:H{number}'))
            book.unload_sheet(name)
        if len(companies) != 5100:
            raise ValueError('unsupported audited SHSE/SZSE scope')
        return dict(contract='alphalake-company-industries-v1',
                    parser_version='damodaran-company-industries-v1',
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    runtime=f'python={platform.python_version()};xlrd={xlrd.__version__}',
                    source_observation_date=None, total_rows=48156,
                    unidentified_rows=unidentified, outside_scope_rows=48156-len(companies),
                    industries=industries, companies=companies)
    finally:
        book.release_resources()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workbook')
    print(json.dumps(alphalake_company_industry_snapshot(parser.parse_args().workbook),
                     ensure_ascii=False, sort_keys=True, allow_nan=False))
