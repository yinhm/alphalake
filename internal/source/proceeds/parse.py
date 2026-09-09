"""两份已审核H股融资公告；净额是发行人估计，不是实际到账或剩余现金。"""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
import pypdf


def parse(path):
    body=Path(path).read_bytes()
    pages=[p.extract_text() for p in pypdf.PdfReader(path).pages]
    text=re.sub(r'\s+','', ''.join(pages))
    if '安克创新科技股份有限公司' not in text or '证券代码：300866' not in text:
        raise ValueError('unreviewed issuer')
    if len(pages)==2 and '关于境外上市外资股（H股）挂牌并上市交易的公告' in text:
        if not all(x in text for x in ['46,632,800股','99.32港元','约为45.23亿港元','2026年7月2日','净额估计']):
            raise ValueError('IPO terms changed')
        event,day,shares,value,locator='ipo','2026-07-02','46632800','4523000000','PDF p1 / estimated net proceeds before over-allotment'
        shares=re.findall(r'全球发售H股总数为([\d,]+)股',text)
        amounts=re.findall(r'净额估计约为([\d.]+)亿港元',text)
        if len(shares)!=1 or len(amounts)!=1: raise ValueError('ambiguous IPO cells')
        shares=shares[0].replace(',','');value=str(int(Decimal(amounts[0])*100000000))
        url='https://static.cninfo.com.cn/finalpage/2026-07-02/1225406962.PDF' 
    elif len(pages)==3 and '关于部分行使超额配股权、稳定价格行动及稳定价格期结束的公告' in text:
        if not all(x in text for x in ['3,443,300股','99.32港元','净额约337.86百万港元','预估承销费用','2026年7月29日','50,076,100']):
            raise ValueError('over-allotment terms changed')
        event,day,shares,value,locator='greenshoe','2026-07-29','3443300','337860000','PDF p1 expected listing date; p2 / estimated additional net proceeds'
        shares=re.findall(r'涉及合计([\d,]+)股H股',text)
        amounts=re.findall(r'净额约([\d.]+)百万港元',text)
        if len(shares)!=2 or len(set(shares))!=1 or len(amounts)!=1: raise ValueError('ambiguous over-allotment cells')
        shares=shares[0].replace(',','');value=str(int(Decimal(amounts[0])*1000000))
        url='https://disc.static.szse.cn/download/disc/disk03/finalpage/2026-07-27/10d2e333-09f5-4b03-a51d-0f043257edff.PDF' 
    else: raise ValueError('unreviewed financing document')
    return dict(contract='alphalake-equity-proceeds-v1',observation_date=day,sha256=hashlib.sha256(body).hexdigest(),
        parser_version='reviewed-proceeds-v1',runtime='pypdf/'+pypdf.__version__,url=url,
        code='300866',event=event,shares=shares,currency='HKD',net_proceeds=value,
        amount_status='issuer_estimate_after_estimated_costs',date_status=('reported_listing_date_not_cash_settlement' if event=='ipo' else 'expected_listing_date_not_cash_settlement'),locator=locator)


if __name__=='__main__':
    print(json.dumps(parse(sys.argv[1]),sort_keys=True,allow_nan=False))
