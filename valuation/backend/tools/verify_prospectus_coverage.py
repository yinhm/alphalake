"""离线复验已人工审核的招股披露范围；仅输出无数值的披露关联，不导入 PDF 金额。"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess


def verify(manifest, workspace, source_audit):
    assert hashlib.sha256(source_audit).hexdigest() == manifest['source_audit_sha256'], 'source audit hash'
    source = {(r['code'], r['period']): r for r in json.loads(source_audit)}
    reviews = []
    checked = []
    for document in manifest['documents']:
        path = workspace / document['local_path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == document['pdf_sha256'], 'PDF hash'
        pages = subprocess.check_output(['pdftotext', '-layout', str(path), '-'], text=True).split('\f')
        for cell in document['cells']:
            context = re.sub(r'\s+', '', '\n'.join(pages[n-1] for n in cell['pages']))
            assert all(re.sub(r'\s+', '', s) in context for s in cell['context']), 'period/scope context'
            assert cell['column_periods'][cell['column']-1] == cell['period'], 'period column'
            lines = [line for line in pages[cell['value_page']-1].splitlines() if re.match(r'^\s*研发费用\s+', line)]
            assert len(lines) == 1, 'ambiguous research expense row'
            amounts = re.findall(r'-?[\d,]+\.\d{2}', lines[0])
            amount = Decimal(amounts[cell['column']-1].replace(',', ''))
            assert amount == Decimal(cell['pdf_amount']), 'PDF amount changed'
            observations = source[(document['code'], cell['period'])]['observations']
            assert len(observations) == 1, 'ambiguous TDX revision'
            observation = observations[0]
            assert observation['field'] == 'research_and_development_expense'
            assert observation['unit'] == 'CNY' and observation['period_basis'] == 'ytd'
            assert observation['state'] == 'source_observation_not_standard_fact'
            value = observation['value']
            bits = observation['source_evidence']['float32_bits']
            assert value > 0 and struct.unpack('<I', struct.pack('<f', value))[0] == bits
            scale = Decimal(cell['scale'])
            assert scale in (1, 10000)
            # Printed two-decimal rounding plus source float32 half-ULP.
            next_value = struct.unpack('<f', struct.pack('<I', bits+1))[0]
            tolerance = Decimal('0.005') * scale + Decimal(str(next_value-value))/2
            delta = Decimal(str(value)) - amount*scale
            assert abs(delta) <= tolerance, f"TDX/PDF conflict: {document['code']} {cell['period']} {delta}"
            reviews.append(dict(code=document['code'], announcement_id=document['announcement_id'],
                period=cell['period'], fields=['research_and_development_expense'], pdf_sha256=document['pdf_sha256'],
                pages=cell['pages'], reviewer=manifest['reviewer'], reviewed_at=manifest['reviewed_at'], action='publish',
                note=cell['note'] + '；仅确认披露关联，数值保留TDX源精度，PDF不作为数值输入。'))
            checked.append(dict(code=document['code'], period=cell['period'], source_value=value,
                                pdf_amount=str(amount), scale=str(scale), difference_CNY=str(delta), tolerance_CNY=str(tolerance)))
    return reviews, checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--source-audit', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    reviews, checked = verify(manifest, args.workspace, args.source_audit.read_bytes())
    args.output.mkdir(exist_ok=True, parents=True)
    for name, rows in [('reviews.json', reviews), ('comparison.json', checked)]:
        (args.output/name).write_text(json.dumps(rows, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(reviewed_cells=len(reviews), companies=len({r['code'] for r in reviews}))))


if __name__ == '__main__':
    main()
