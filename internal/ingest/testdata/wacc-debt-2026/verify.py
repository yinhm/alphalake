"""从固定原文重新提取期限表，逐字节核对生产附注供给文件。"""
import hashlib
import json
from decimal import Decimal
from pathlib import Path
import re
import sys
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
PDF = ROOT.parent/'anker-valuation-2026/1225533054.pdf'


def build():
    sha = hashlib.sha256(PDF.read_bytes()).hexdigest()
    # 哈希沿用既有财报证据，防止替换 PDF 后随脚本自动重签。
    existing = json.loads((ROOT.parent/'valuation-integration-2026/supplements.json').read_text())
    assert sha == next(r['pdf_sha256'] for r in existing if r['announcement_id']=='1225533054')
    text = PdfReader(PDF).pages[153].extract_text()
    assert '2026 年06 月30 日未折现的合同现金流量' in text
    assert '资产负债表' in text and '单位：元' in text and '现行利率' in text
    rows = []
    for key, label in [('short','短期借款'),('long','长期借款'),('lease','租赁负债'),('bond','应付债券')]:
        pattern = re.escape(label) + (r'\s*\(含一年内\s*到期部分\)' if key!='short' else '')
        matches = re.findall(pattern+r'\s+((?:[\d,]+\.\d{2}|-)(?:\s+(?:[\d,]+\.\d{2}|-)){5})', text)
        assert len(matches)==1, key
        values = [v.replace(',','') if v!='-' else '0.00' for v in matches[0].split()]
        assert sum(map(Decimal,values[:4]))==Decimal(values[4]), key
        for column, value in zip(['0_1','1_2','2_5','5_plus','total','book'],values):
            rows.append(dict(code='300866',period='2026-06-30',item=f'debt_cf_{key}_{column}',value=value,
                unit='CNY',period_basis='instant',scope='consolidated_note_component',announcement_id='1225533054',
                pdf_sha256=sha,pdf_page=154,reviewer='alphalake-reviewed-sample',
                review_note=f'liquidity_risk_2026H1/{key}/{column}; contractual cashflows including interest; dash means no contractual amount in this bucket, not an undisclosed field; excludes trade payables and derivatives'))
    moutai=ROOT.parent/'moutai-valuation-2026/1225475868.pdf'
    assert hashlib.sha256(moutai.read_bytes()).hexdigest()=='0e10aa26be46b1cf3cd03f06e834c7fb98d5dd0d661b96f8fddd4af7e846a4f6'
    disclosure=re.sub(r'\s+','',PdfReader(moutai).pages[90].extract_text())
    assert '不以公允价值计量的金融资产和金融负债的公允价值情况' in disclosure
    assert '租赁负债等，其账面价值与公允价值差异较小。' in disclosure
    return json.dumps(rows,ensure_ascii=False,indent=2)+'\n' 


if __name__=='__main__':
    output = build()
    if sys.argv[1:]==['--write']:
        (ROOT/'supplements.json').write_text(output)
    else:
        assert not sys.argv[1:]
        assert (ROOT/'supplements.json').read_text()==output, 'debt evidence differs'
        print('24 debt maturity cells verified against original PDF page 154')
