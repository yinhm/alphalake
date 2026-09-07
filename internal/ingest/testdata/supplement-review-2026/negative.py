"""正向上游验证之后执行；只验证缺项、篡改和时点拒绝，不替代 PDF 重提取。"""
import importlib.util
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def verify():
    review=module('review_negative',ROOT/'verify.py')
    chain=module('chain_negative',ROOT.parent/'valuation-chain-2026/verify.py')
    cases=[(chain,'resolved.csv','missing'),(chain,'resolved.csv','value'),(chain,'resolved.csv','unit'),
           (review,'filings.csv','future'),(review,'filings.csv','hash')]
    for target,filename,mode in cases:
        original=target.read
        def altered(path):
            rows=original(path)
            if path.name==filename:
                if filename=='resolved.csv':
                    selected=next(r for r in rows if r['route']=='cninfo_note')
                    if mode=='missing':rows.remove(selected)
                    elif mode=='value':selected['value']=str(float(selected['value'])+1)
                    else:selected['unit']='USD'
                else:
                    selected=next(r for r in rows if r['announcement_id']=='1225475863')
                    if mode=='future':selected['available_at']='2026-09-06T00:00:01+00:00'
                    else:selected['pdf_sha256']='0'*64
            return rows
        with patch.object(target,'read',altered):
            try:target.build()
            except AssertionError:pass
            else:raise AssertionError('accepted invalid supplement '+mode)
    review.build();chain.build()
    print('通过：补充缺项/金额/币种、独立公告未来时点/PDF 哈希五路拒绝，恢复后通过。')


if __name__=='__main__':verify()
