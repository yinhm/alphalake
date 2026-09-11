"""真实fundgrEB原表：经营利润增长不可充当收入增长。"""
import hashlib
from pathlib import Path

import pytest
import xlrd

from data_sources.damodaran_parsers.fundgr_parser import parse_fundgr
from data_sources.damodaran_store import DamodaranStore

ROOT = Path(__file__).resolve().parents[2]/'knowledge_base/damodaran'
GLOBAL_SHA = 'd180bb311b2b9370f69ce51a5408a588d247e336bbd6078709b0c21093ad72dc'


def test_real_regions_preserve_ebit_growth_and_typed_lookup():
    assert hashlib.sha256((ROOT/'fundgrEBGlobal.xls').read_bytes()).hexdigest() == GLOBAL_SHA
    paths = sorted(ROOT.glob('fundgrEB*.xls'))
    assert len(paths) == 8
    checked = 0
    for path in paths:
        sheet = xlrd.open_workbook(path).sheet_by_name('Industry Averages')
        assert sheet.cell_value(7,4) == 'Expected Growth in EBIT'
        parsed = parse_fundgr(path)
        for row in range(8,sheet.nrows):
            name = sheet.cell_value(row,0)
            if name not in parsed:
                continue
            assert 'revenue_growth' not in parsed[name]
            if sheet.cell_type(row,4) == xlrd.XL_CELL_NUMBER:
                assert parsed[name]['expected_ebit_growth'] == sheet.cell_value(row,4)
                checked += 1
        store = DamodaranStore()
        store._merge_industry_data('Global','fundgrEB',parsed)
        reference = store.lookup_industry('Computers/Peripherals','Global')
        assert reference is not None and reference.revenue_growth is None
        assert reference.expected_ebit_growth == parsed['Computers/Peripherals']['expected_ebit_growth']
        assert reference.model_dump()['expected_ebit_growth'] == reference.expected_ebit_growth
    assert checked == 681


def test_renamed_source_header_is_rejected(tmp_path):
    raw = (ROOT/'fundgrEBGlobal.xls').read_bytes()
    assert b'Expected Growth in EBIT' in raw
    path = tmp_path/'bad.xls'
    path.write_bytes(raw.replace(b'Expected Growth in EBIT',b'Expected Growth in SALE'))
    with pytest.raises(ValueError,match='missing Expected Growth in EBIT header'):
        parse_fundgr(path)
