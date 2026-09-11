"""定向核对已评分收入案例；原文不覆盖TDX，不改预测规则。"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import re

from pypdf import PdfReader
from tools.audit_tdx_normalized_scope import ROOT, compact, digest
from tools.backtest_tdx_history import value


def audit(config, raw_dir=None):
    if config['audit_id'] != 'tdx-half-growth-source-review-v1':
        raise ValueError('unsupported audit')
    ref = config['source']; raw = (ROOT / ref['path']).read_bytes()
    if digest(raw) != ref['sha256']: raise ValueError('source hash differs')
    records = json.loads(raw)['records']
    source = {(r['code'], r['period']): r for r in records}
    if len(source) != len(records): raise ValueError('duplicate source record')
    checks = []; phrases = 0
    for doc in config['documents']:
        raw = (ROOT / doc['selected_path']).read_bytes()
        if digest(raw) != doc['selected_sha256']: raise ValueError('PDF hash differs')
        pdf = PdfReader(ROOT / doc['selected_path'])
        if len(pdf.pages) != len(doc['original_pages']): raise ValueError('page count differs')
        texts = {n: pdf.pages[i].extract_text() for i, n in enumerate(doc['original_pages'])}
        if raw_dir is not None:
            full = Path(raw_dir) / Path(doc['full_path']).name
            if digest(full.read_bytes()) != doc['full_sha256']: raise ValueError('full PDF hash differs')
            original = PdfReader(full)
            if any(original.pages[n-1].extract_text() != text for n, text in texts.items()):
                raise ValueError('selected text differs')
        for page, phrase in doc['contains']:
            if compact(phrase) not in compact(texts[page]): raise ValueError('business phrase missing')
            phrases += 1
        for check in doc['checks']:
            text = compact(texts[check['page']]).split(compact(check['section']), 1)[1]
            text = text.split(compact(check['anchor']), 1)[1]
            amounts = re.findall(r'-?\d[\d,]*\.\d{2}', text)[:len(check['values'])]
            amounts = [Decimal(v.replace(',', '')) for v in amounts]
            if amounts != [Decimal(v) for v in check['values']]: raise ValueError('PDF amounts differ')
            if len(amounts) != len(check['source_period_groups']): raise ValueError('source group count differs')
            for amount, periods in zip(amounts, check['source_period_groups']):
                rows = [source[doc['code'], p] for p in periods]
                values = [value(r, 'FN230') for r in rows]
                if any(v <= 0 for v in values): raise ValueError('nonpositive source revenue')
                bound = sum((value({'bits': {'FN230': r['bits']['FN230']+1}}, 'FN230')-v)/2 for r, v in zip(rows, values)) + Decimal('.02')
                total = sum(values)
                if abs(total-amount) > bound: raise ValueError('source revenue differs from PDF')
                checks.append(dict(code=doc['code'], periods=periods, pdf_cny=str(amount),
                                   tdx_cny=str(total), difference_cny=str(total-amount),
                                   rounding_bound_cny=str(bound), source_inputs=[dict(period=r['period'], artifact=r['artifact'], bits=r['bits']['FN230']) for r in rows]))
    return dict(audit_id=config['audit_id'], checks=checks, business_phrases=phrases,
                full_archive_checked=raw_dir is not None,
                boundary='post_hoc_partial_source_audit_not_forecast_validation_or_strict_PIT')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path); parser.add_argument('--raw-dir', type=Path)
    args = parser.parse_args(); raw = args.config.read_bytes()
    result = audit(json.loads(raw), args.raw_dir)
    result['evidence'] = dict(config_sha256=digest(raw), code_sha256=digest(Path(__file__).read_bytes()))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__': main()
