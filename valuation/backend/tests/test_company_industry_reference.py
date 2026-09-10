"""真实工作簿的重复表示一致性；不是独立的公司业务分类审核。"""
import gzip
from pathlib import Path

import pytest

from data_sources.damodaran_parsers import company_industry_parser as parser


def test_company_industry_archive_and_disagreeing_sheet(tmp_path, monkeypatch):
    archive = Path(__file__).resolve().parents[3] / 'internal/source/damodaran/testdata/indname.xls.gz'
    workbook = tmp_path / 'indname.xls'
    workbook.write_bytes(gzip.decompress(archive.read_bytes()))
    snapshot = parser.alphalake_company_industry_snapshot(workbook)
    companies = {c['ticker']: c for c in snapshot['companies']}
    assert len(companies) == 5100
    assert snapshot['source_observation_date'] is None
    assert snapshot['unidentified_rows'] == 12
    assert companies['SZSE:300866']['industry'] == 'Computers/Peripherals'
    assert companies['SHSE:600519']['industry'] == 'Beverage (Alcoholic)'
    assert companies['SZSE:000553']['country'] == 'Israel'
    assert companies['SHSE:601390']['country'] == 'Hong Kong'
    assert companies['SHSE:601888']['sic_code'] == 0.0
    assert isinstance(companies['SHSE:601888']['sic_code'], float)
    assert all(c.startswith(('SHSE:', 'SZSE:')) for c in companies)

    original_open = parser.xlrd.open_workbook

    def altered_open(*args, **kwargs):
        book = original_open(*args, **kwargs)
        original_sheet = book.sheet_by_name

        def altered_sheet(name):
            sheet = original_sheet(name)
            if name == 'By geography':
                original_values = sheet.row_values

                def altered_values(row):
                    values = original_values(row)
                    if row == 1:
                        values[2] = 'invented industry'
                    return values

                sheet.row_values = altered_values
            return sheet

        book.sheet_by_name = altered_sheet
        return book

    monkeypatch.setattr(parser.xlrd, 'open_workbook', altered_open)
    with pytest.raises(ValueError, match='sheets disagree'):
        parser.alphalake_company_industry_snapshot(workbook)
