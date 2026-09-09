"""已审核的安克月报和茅台半年报股份类别；不把通用 PDF 解析当语义审核。"""
import hashlib
import json
import platform
import re
import sys
import subprocess
from pathlib import Path
from pypdf import PdfReader, __version__


def one(pattern, text):
    rows = re.findall(pattern, text)
    if len(rows) != 1:
        raise ValueError('missing/ambiguous share evidence: '+pattern)
    return rows[0]


def parse(path):
    body = Path(path).read_bytes()
    pages = [p.extract_text() for p in PdfReader(path).pages]
    runtime=f'python={platform.python_version()};pypdf={__version__}'
    if len(pages)==11:
        # 此月报缺 ToUnicode；使用 Poppler 的标准 Adobe 字符映射，不手写字形替换。
        pages=subprocess.run(['pdftotext','-layout',str(path),'-'],check=True,capture_output=True,timeout=30).stdout.decode('utf-8').split('\f')
        if not pages[-1].strip(): pages.pop()
        runtime += ';'+subprocess.run(['pdftotext','-v'],check=True,capture_output=True,timeout=5).stderr.decode().splitlines()[0]
    if len(pages) == 11 and '安克創新科技股份有限公司' in pages[0]:
        if not re.search(r'截至月份:\s+2026年8月31日',pages[0]) or '2026年9月4日' not in pages[0]:
            raise ValueError('unreviewed monthly return date')
        rows = re.findall(r'本月底結存\s+([\d,]+)\s+(\d+)\s+([\d,]+)', pages[2])
        if len(rows) != 2 or '00668' not in pages[2] or '300866' not in pages[2]:
            raise ValueError('share-class identity/table mismatch')
        classes = []
        for (outstanding, treasury, issued), kind, mic, currency, symbol in zip(rows, ['H','A'], ['XHKG','XSHE'], ['HKD','CNY'], ['00668','sz300866']):
            o,t,i = [int(x.replace(',','')) for x in (outstanding,treasury,issued)]
            if i-t != o or o<=0: raise ValueError('issued minus treasury identity')
            classes.append(dict(kind=kind, mic=mic, currency=currency, symbol=symbol,
                issued=str(i), treasury=str(t), outstanding=str(o), locator=f'PDF p3 / II / {kind} / 本月底結存'))
        total=int(one(r'本月底法定/註冊股本總額：\s+RMB\s+([\d,]+)',pages[0]).replace(',',''))
        if total!=sum(int(c['issued']) for c in classes): raise ValueError('issuer total differs from A/H sum')
        code, name, day = '300866', '安克创新科技股份有限公司', '2026-08-31'
        url='https://disc.static.szse.cn/disc/disk03/finalpage/2026-09-04/2db3ed6a-0a7e-4f37-82eb-a03ec2045ec8.PDF'
    elif len(pages)==110 and '贵州茅台酒股份有限公司2026年半年度报告' in pages[0]:
        text=re.sub(r'\s+','',pages[21])
        rows=re.findall(r'1,252,270,215100-2,188,614-2,188,614([\d,]+)100',text)
        if len(rows)!=3 or len(set(rows))!=1 or '人民币普通股' not in text:
            raise ValueError('Moutai class/total reconciliation')
        out=int(rows[0].replace(',',''))
        if 1252270215-2188614!=out or '完成注销' not in pages[22] or '股本减少系公司完成股份回购并注销' not in pages[71]:
            raise ValueError('cancellation not confirmed')
        # 库存股不是从空单元格补零：回购全部注销的股份数量桥接与完成说明共同确认。
        classes=[dict(kind='A',mic='XSHG',currency='CNY',symbol='sh600519',issued=str(out),treasury='0',outstanding=str(out),locator='PDF p22 股份变动表; p23/p72 已完成全数回购注销')]
        code,name,day='600519','贵州茅台酒股份有限公司','2026-06-30'
        url='https://static.cninfo.com.cn/finalpage/2026-08-15/1225475868.PDF'
    else:
        raise ValueError('unreviewed issuer document')
    return dict(contract='alphalake-share-classes-v1',observation_date=day,sha256=hashlib.sha256(body).hexdigest(),
        parser_version='reviewed-issuer-capital-v1',runtime=runtime,
        code=code,legal_name=name,url=url,classes=classes)


if __name__=='__main__':
    print(json.dumps(parse(sys.argv[1]),sort_keys=True,allow_nan=False))
