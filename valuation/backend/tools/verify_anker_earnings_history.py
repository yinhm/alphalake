"""复用2024原包/公告证据，核验安克历史调整EBIT的六个源分量。"""
import argparse
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import re
import struct
import zipfile

from pypdf import PdfReader
from tools.tdx_research_source import source_value as value
from tools.verify_anker_cash_history import verify as verify_parent

LABELS = dict(FN86='三、营业利润（亏损以“－”号填列）', FN305='其中：利息费用', FN306='利息收入',
              FN83='投资收益（损失以“－”号填列）七、47', FN82='公允价值变动收益（损失以“－”号填列）七、48',
              FN301='资产处置收益（损失以“－”号填列）七、51')
FINANCE = ['贷款及应付款项的利息支出', '租赁负债的利息支出', '减：存款及应收款项的利息收入']


def verify(directory):
    parent = verify_parent(directory)  # 公告身份、FN314日期、原文/原包哈希及整条裁剪记录。
    ledger = json.loads((directory/'earnings-evidence.json').read_bytes())
    reports = {r['period']: r for r in json.loads((directory/'reports.json').read_bytes())}
    source = json.loads((directory/'source-snapshot.json').read_bytes())
    if ledger['code'] != '300866' or [r['period'] for r in ledger['reports']] != ['2024-06-30', '2024-09-30', '2024-12-31']:
        raise ValueError('earnings identity/period set differs')
    result = []
    for item in ledger['reports']:
        period = item['period']; report = reports[period]
        quarterly = period == '2024-09-30'
        labels = {f:re.sub(r'七、\d+', '', label).replace('处置收益（损失以“－”', '处置收益（损失以“-”') for f,label in LABELS.items()} if quarterly else LABELS
        if item['announcement_id'] != report['announcement_id'] or {r['field']:r['label'] for r in item['rows']} != labels or len(item['rows']) != 6:
            raise ValueError('earnings field/report identity differs')
        if [r['label'] for r in item['finance_rows']] != ([] if quarterly else FINANCE):
            raise ValueError('interest component scope differs')
        pdf = PdfReader(directory/report['file'])
        text = lambda page: re.sub(r'\s+', '', pdf.pages[page-1].extract_text())
        main, header = text(item['main_page']), text(item['header_page'])
        suffix = '年半年度' if period == '2024-06-30' else '年度'
        expected = ('2、合并年初到报告期末利润表', '单位：元', '项目本期发生额上期发生额', '2024年第三季度报告') if quarterly else ('3、合并利润表', '单位：元', '项目附注2024'+suffix+'2023'+suffix)
        if item['main_page']-item['header_page'] not in (0,1) or any(s not in header for s in expected):
            raise ValueError('consolidated statement unit/period scope differs')
        note = ''
        if not quarterly:
            note = text(item['note_page'])
            if any(s not in note for s in ('45、财务费用', '单位：元', '项目2024'+suffix+'2023'+suffix, '46、其他收益')):
                raise ValueError('finance note unit/period scope differs')
            note = note.split('45、财务费用', 1)[1].split('46、其他收益', 1)[0]
        def extract(row, page):
            matches = re.findall(re.escape(row['label'])+r'(-?[\d,]+\.\d{2})(-?[\d,]+\.\d{2})', page)
            if len(matches) != 1 or [v.replace(',', '') for v in matches[0]] != row['values']:
                raise ValueError('PDF earnings amount differs: '+row['label'])
            return [D(v) for v in row['values']]
        amounts = {row['field']:extract(row, main) for row in item['rows']}
        finance = [extract(row, note) for row in item['finance_rows']]
        for column in (() if quarterly else (0, 1)):
            if finance[0][column]+finance[1][column] != amounts['FN305'][column] or finance[2][column] != amounts['FN306'][column]:
                raise ValueError('loan/lease interest or deposit income does not match main statement')
        row = next(r for r in source['records'] if r['code'] == '300866' and r['period'] == period)
        name = 'gpcw'+period.replace('-', '')
        with zipfile.ZipFile(directory/(name+'.zip')) as z:
            record = z.read(name+'.dat')[31:]  # 单条布局已由父校验器核验。
        for field, columns in amounts.items():
            bits = struct.unpack('<I', struct.pack('<f', float(columns[0])))[0]
            if row['bits'][field] != bits or struct.unpack_from('<I', record, (int(field[2:])-1)*4)[0] != bits:
                raise ValueError('TDX earnings bits differ: '+field)
            result.append(dict(period=period, field=field, pdf_amount_cny=str(columns[0]), source_bits=bits,
                               source_value_cny=str(value(row, field)), source_minus_pdf_cny=str(value(row, field)-columns[0])))
    return dict(status='historical_earnings_source_verified_not_materialized', code='300866', source_values=result,
                printed_amounts=48, parent_source_values_verified=len(parent['source_values']),
                evidence={name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in ('earnings-evidence.json', 'reports.json', 'packages.json', 'source-snapshot.json')},
                boundary='18 current-column TDX values match original consolidated statements; 18 comparative cells and 12 interest-note cells checked; H1/FY FN305 includes loan/payable and lease interest; Q3 has no matching component-note assertion; no global historical scope or mapping extension claimed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
