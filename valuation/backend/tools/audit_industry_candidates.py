"""只读归组申万映射候选并比较官方地域Beta，不发布政策或运行估值。"""
import argparse
import json
import math
from pathlib import Path
import sqlite3

import xlrd

from data_sources.damodaran_parsers.beta_parser import parse_betas
from data_sources.damodaran_parsers.company_industry_parser import alphalake_company_industry_snapshot
from tools.audit_reference_coverage import audit
from tools.export_alphalake_sqlite import digest


def regional_betas(directory, industries):
    result = {}
    for region, filename, label in [('US', 'betas.xls', 'US companies'),
            ('Global', 'betaGlobal.xls', 'Global'), ('China', 'betaChina.xls', 'China'),
            ('Emerging', 'betaemerg.xls', 'Emerging Markets')]:
        path = Path(directory) / filename
        workbook = xlrd.open_workbook(path)
        try:
            sheet = workbook.sheet_by_name('Industry Averages')
            headers = ['Industry Name', 'Number of firms', 'Beta', 'D/E Ratio',
                'Effective Tax rate', 'Unlevered beta', 'Cash/Firm value',
                'Unlevered beta corrected for cash']
            if (sheet.cell_value(2, 5) != label or [str(v).strip() for v in sheet.row_values(9)[:8]] != headers
                    or sheet.cell_value(0, 0) != 'Date updated:'
                    or sheet.cell_type(0, 1) != xlrd.XL_CELL_DATE):
                raise ValueError('unsupported beta region/headers/date: ' + filename)
            names = [sheet.cell_value(r, 0) for r in range(10, sheet.nrows)]
            if len(names) != len(set(names)):
                raise ValueError('duplicate beta industry: ' + filename)
            parsed = parse_betas(path)
            rows = {}
            for industry in sorted(industries):
                value = parsed.get(industry)
                if value is not None:
                    if (any(v is not None and not math.isfinite(v) for v in value.values())
                            or value['number_of_firms'] is None or value['number_of_firms'] <= 0):
                        raise ValueError('invalid beta sample/value: ' + industry)
                    value = dict(value, source_locator=f'Industry Averages!A{names.index(industry)+11}:H{names.index(industry)+11}')
                rows[industry] = value  # 缺地域/行业不回退，不生成公司参数。
            result[region] = dict(filename=filename, sha256=digest(path),
                source_url='https://pages.stern.nyu.edu/~adamodar/pc/datasets/' + filename,
                source_region=label, unlevering_tax_choice=sheet.cell_value(7, 5),
                unlevering_tax_rate=sheet.cell_value(8, 5), observation_date=xlrd.xldate_as_datetime(
                    sheet.cell_value(0, 1), workbook.datemode).date().isoformat(),
                industries=rows)
        finally:
            workbook.release_resources()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sqlite')
    parser.add_argument('coverage')
    parser.add_argument('company_workbook')
    parser.add_argument('beta_directory')
    args = parser.parse_args()
    with sqlite3.connect(Path(args.sqlite).resolve().as_uri()+'?mode=ro', uri=True) as conn:
        result = audit(conn, json.loads(Path(args.coverage).read_text()),
            alphalake_company_industry_snapshot(args.company_workbook))
    categories = result['industry_categories']
    result.update(contract='alphalake-industry-candidates-v1',
        sqlite_sha256=digest(args.sqlite), coverage_sha256=digest(args.coverage),
        category_count=len(categories),
        priority_category_count=sum(bool(c['priority_single_candidate_tickers']) for c in categories),
        regional_betas=regional_betas(args.beta_directory,
            {i for c in categories for i in c['peer_industry_counts']}),
        policy_status='pending_review_not_adopted',
        boundary='同业成员及地域值供类别审核；不认证经济适配，不推断国家，不改默认或引擎。')
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
