"""开源目录定位，既有原文独立核验；标准余额与累计利润供给验收。"""
from decimal import Decimal as D
import csv
import importlib.util
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
FIELDS = {'FN19': (None, 'current_financial_maturity'), 'FN20': ('other_current_assets', 'other_current_assets'), 'FN27': (None, 'fixed_assets'), 'FN28': (None, 'cip'), 'FN33': (None, 'intangibles'), 'FN37': ('tax_dta', None), 'FN50': ('other_payables', 'other_payable'), 'FN53': ('other_current_liabilities', 'other_current_liabilities'), 'FN60': ('tax_dtl', None), 'FN95': ('net_income', 'net_income'), 'FN96': (None, 'parent_income'), 'FN97': (None, 'minority_income')}


def verify(write=False):
    spec = importlib.util.spec_from_file_location('fn_evidence', ROOT.parent/'earnings-working-capital-2026/verify.py')
    evidence = importlib.util.module_from_spec(spec); spec.loader.exec_module(evidence)
    evidence.verify(write, root=ROOT, fields=FIELDS, expected_count=41)
    with (ROOT.parent/'valuation-chain-2026/facts.csv').open() as f:
        facts = {(r['code'],r['field'],r['period']):r for r in csv.DictReader(f)}
    with (ROOT.parent/'valuation-chain-2026/windows.csv').open() as f:
        windows = {(r['code'],r['field']):r for r in csv.DictReader(f)}
    for code in ('300866','600519'):
        for field in FIELDS:
            row=windows[code,field]
            if (code,field) in {('300866','FN19'),('300866','FN28')}:
                assert row['value']=='' and row['coverage_status']!='complete'
                continue
            periods=[('2026-06-30',1)] if int(field[2:])<80 else [('2025-12-31',1),('2026-06-30',1),('2025-06-30',-1)]
            expected=sum((D(facts[code,field,p]['value'])*sign for p,sign in periods),D(0))
            assert row['coverage_status']=='complete' and D(row['value'])==expected
    # 原文的母公司＋少数股东＝净利润；该恒等式只作语义一致性检查，不冒充独立证据。
    with (ROOT/'values.csv').open() as f:
        printed={(r['code'],r['period'],r['field']):D(r['pdf_value']) for r in csv.DictReader(f)}
    for period in ('2025-06-30','2025-12-31','2026-06-30'):
        assert printed['600519',period,'FN95']==printed['600519',period,'FN96']+printed['600519',period,'FN97']
    print('通过：12 个余额/累计利润 FN、41 个原文金额及源位、24 个查询窗口（22 完整、2 缺失）。')


if __name__=='__main__':
    subprocess.run([sys.executable,str(ROOT.parent/'valuation-chain-2026/verify.py')],check=True)
