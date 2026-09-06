"""税费现金、营运调节和研发的标准 TTM 供给；不冒充已分类估值 FCFF。"""
import csv
from decimal import Decimal as D
import importlib.util
import io
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
FIELDS = {'FN99': ('cash_tax_refunds', None), 'FN104': ('cash_taxes_paid', 'tax_cash'),
          'FN146': ('cf_inventory', 'cf_inventory'), 'FN147': ('cf_receivables', 'cf_receivables'),
          'FN148': ('cf_payables', 'cf_payables'), 'FN304': ('rd', 'rd')}


def verify(write=False):
    spec = importlib.util.spec_from_file_location('fn_evidence', ROOT.parent/'earnings-working-capital-2026/verify.py')
    evidence = importlib.util.module_from_spec(spec); spec.loader.exec_module(evidence)
    evidence.verify(write, root=ROOT, fields=FIELDS, expected_count=33)
    with (ROOT.parent/'valuation-chain-2026/windows.csv').open() as f:
        windows = {(r['code'],r['field']):r for r in csv.DictReader(f)}
    with (ROOT.parent/'valuation-chain-2026/facts.csv').open() as f:
        facts = {(r['code'],r['field'],r['period']):r for r in csv.DictReader(f)}
    output=[]
    for code in ('300866','600519'):
        for field in FIELDS:
            r=windows[code,field]
            if (code,field)==('600519','FN99'):
                assert r['coverage_status']!='complete' and r['value']=='' and r['available_inputs']=='0'
            else:
                expected=sum((D(facts[code,field,p]['value'])*sign for p,sign in
                    [('2025-12-31',1),('2026-06-30',1),('2025-06-30',-1)]),D(0))
                assert r['coverage_status']=='complete' and D(r['value'])==expected
            output.append(dict(code=code,field=field,canonical_field=r['canonical_field'],period='TTM-2026-06-30',
                value=r['value'],unit='CNY',coverage_status=r['coverage_status'],
                available_inputs=r['available_inputs'],required_inputs=r['required_inputs'],
                information_as_of='2026-09-06T00:00:00+00:00',statement_scope='provider_default'))
        contribution=sum((D(windows[code,f]['value']) for f in ('FN146','FN147','FN148')),D(0))
        output.append(dict(code=code,field='FN146+FN147+FN148',canonical_field='working_capital_cashflow_contribution',
            period='TTM-2026-06-30',value=str(contribution),unit='CNY',
            coverage_status='cashflow_reconciliation_not_classified_valuation_wc',available_inputs='3',required_inputs='3',
            information_as_of='2026-09-06T00:00:00+00:00',statement_scope='provider_default'))
    out=io.StringIO();w=csv.DictWriter(out,fieldnames=list(output[0]),lineterminator='\n');w.writeheader();w.writerows(output)
    if write:(ROOT/'inputs.csv').write_text(out.getvalue())
    else:assert (ROOT/'inputs.csv').read_text()==out.getvalue(),'cash/RD inputs differ'
    print('通过：12 个标准 TTM 输入槽位（11 完整、1 缺失）及两行现金流调节合计；没有生成历史 FCFF。')


if __name__=='__main__':
    subprocess.run([sys.executable,str(ROOT.parent/'valuation-chain-2026/verify.py')],check=True)
