"""金融明细标准供给：显式万元倍率、期末/累计、财报与源位核对。"""
import importlib.util
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
FIELDS = {'FN9': ('trading_assets', None), 'FN59': ('extra_provisions', None), 'FN299': ('extra_convertible_equity_book', None), 'FN403': (None, 'interbank_assets'), 'FN409': (None, 'reverse_repo'), 'FN411': (None, 'loans'), 'FN413': (None, 'deposits_liability'), 'FN430': (None, 'debt_investments'), 'FN431': (None, 'other_debt_investments'), 'FN433': (None, 'funds'), 'FN434': (None, 'contract_liabilities'), 'FN437': (None, 'notes_receivable'), 'FN506': (None, 'financial_interest_income'), 'FN509': (None, 'financial_interest_expense'), 'FN510': (None, 'financial_fees'), 'FN520': (None, 'credit_impairment'), 'FN579': ('cf_property_depreciation', None)}
OPTIONS = {'FN9': ('instant', 1), 'FN59': ('instant', 1), 'FN299': ('instant', 1), 'FN403': ('instant', 10000), 'FN409': ('instant', 10000), 'FN411': ('instant', 10000), 'FN413': ('instant', 10000), 'FN430': ('instant', 10000), 'FN431': ('instant', 10000), 'FN433': ('instant', 10000), 'FN434': ('instant', 10000), 'FN437': ('instant', 10000), 'FN506': ('ytd', 10000), 'FN509': ('ytd', 10000), 'FN510': ('ytd', 10000), 'FN520': ('ytd', 10000), 'FN579': ('ytd', 10000)}


def verify(write=False):
    spec=importlib.util.spec_from_file_location('fn_evidence',ROOT.parent/'earnings-working-capital-2026/verify.py')
    evidence=importlib.util.module_from_spec(spec);spec.loader.exec_module(evidence)
    evidence.verify(write,root=ROOT,fields=FIELDS,expected_count=38,field_options=OPTIONS)


if __name__=='__main__':
    subprocess.run([sys.executable,str(ROOT.parent/'valuation-chain-2026/verify.py')],check=True)
