"""复核已评分利润率案例及年度可比性，不选择或改写预测规则。"""
import argparse
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from pypdf import PdfReader, __version__ as pypdf_version
from tools.backtest_tdx_history import value

ROOT=Path(__file__).resolve().parents[3]
BASE='zero_growth_current_margin'
CANDIDATE='zero_growth_gradual_five_fy_margin'


def digest(raw):return hashlib.sha256(raw).hexdigest()
def compact(text):return re.sub(r'\s+','',text)


def audit(config,raw_dir=None):
    if config['audit_id']!='tdx-normalized-margin-scope-review-v1':raise ValueError('unsupported audit')
    data={}
    for key,ref in config['inputs'].items():
        raw=(ROOT/ref['path']).read_bytes()
        if digest(raw)!=ref['sha256']:raise ValueError(key+' hash differs')
        data[key]=json.loads(raw)
    parent={(r['code'],r['origin'],r['horizon']):r for r in data['parent']['results']}
    scores=defaultdict(Decimal);counts=defaultdict(int)
    for r in data['gradual']['results']:
        if r['status']!='evaluated':continue
        old=parent[r['code'],r['origin'],r['horizon']]
        scores[r['code']]+=(abs(Decimal(str(r['errors'][CANDIDATE]['ebit_error_million_cny'])))-abs(Decimal(str(r['errors'][BASE]['ebit_error_million_cny']))))/Decimal(str(old['actual']['revenue']))
        counts[r['code']]+=1
    ranking=sorted(scores,key=lambda c:(-scores[c],c));selected=ranking[:2]+sorted(scores,key=lambda c:(scores[c],c))[:2]
    if set(selected)!={d['code'] for d in config['documents']}:raise ValueError('diagnostic selection differs')
    source={(r['code'],r['period']):r for r in data['source']['records']}
    if len(source)!=len(data['source']['records']):raise ValueError('duplicate source record')
    cases=[];amount_checks=0
    for doc in config['documents']:
        path=ROOT/doc['selected_path']
        if digest(path.read_bytes())!=doc['selected_sha256']:raise ValueError('selected PDF hash differs')
        pdf=PdfReader(path)
        if len(pdf.pages)!=len(doc['original_pages']):raise ValueError('selected page count differs')
        texts={n:pdf.pages[i].extract_text() for i,n in enumerate(doc['original_pages'])}
        if raw_dir is not None:
            raw_path=Path(raw_dir)/Path(doc['full']['path']).name
            if digest(raw_path.read_bytes())!=doc['full']['sha256']:raise ValueError('full PDF hash differs')
            original=PdfReader(raw_path)
            if any(original.pages[n-1].extract_text()!=text for n,text in texts.items()):raise ValueError('full/selected page text differs')
        for check in doc['checks']:
            text=compact(texts[check['page']]);anchor=compact(check['anchor'])
            if check.get('section'):text=text.split(check['section'],1)[1]
            parts=text.split(anchor)
            if len(parts)<=check['occurrence']+1:raise ValueError('PDF anchor missing')
            after=parts[check['occurrence']+1]
            found=re.findall(r'-?\d[\d,]*\.\d{2}',after)[:len(check['values'])]
            if [Decimal(v.replace(',','')) for v in found]!=[Decimal(v) for v in check['values']]:raise ValueError(f"PDF amounts differ: {doc['code']} page {check['page']} {anchor}: {found}")
            amount_checks+=len(found)
        for page,phrase in doc['contains']:
            if compact(phrase) not in compact(texts[page]):raise ValueError('PDF business evidence missing')
        years={}
        checked_values={v for check in doc['checks'] for v in check['values']}
        for year,reported in doc['annual_revenue'].items():
            if not set(reported.values())<=checked_values:raise ValueError('annual amount lacks PDF check')
            rows=[source[doc['code'],year+'-'+suffix] for suffix in ('03-31','06-30','09-30','12-31')]
            values=[value(r,'FN230') for r in rows];total=sum(values)
            # Four independently rounded positive float32 quarters; annual decimal cents are separate evidence.
            if any(v<=0 for v in values):raise ValueError('nonpositive quarter in precision check')
            tolerance=sum((value({'bits':{'FN230':r['bits']['FN230']+1}},'FN230')-v)/2 for r,v in zip(rows,values))+Decimal('.02')
            if abs(total-Decimal(reported['original']))>tolerance:raise ValueError('quarter sum differs from original annual revenue')
            item=dict(tdx_quarter_sum_cny=str(total),pdf_original_cny=reported['original'],source_rounding_bound_cny=str(tolerance),source_inputs=[dict(period=r['period'],artifact=r['artifact'],bits=r['bits']['FN230']) for r in rows])
            if 'restated' in reported:
                difference=Decimal(reported['restated'])-Decimal(reported['original'])
                if abs(total-Decimal(reported['restated']))<=tolerance:raise ValueError('restatement not distinguishable')
                item.update(pdf_restated_cny=reported['restated'],reported_restatement_cny=str(difference),status='source_matches_pre_restatement_not_comparable_latest')
            else:item['status']='source_sum_consistent_with_reported_annual_within_rounding'
            years[year]=item
        cases.append(dict(code=doc['code'],evaluated_windows=counts[doc['code']],signed_primary_error_contribution_pp=str(scores[doc['code']]*100),annual_revenue=years))
    names={s['code']:s['name'] for s in data['protocol']['samples']}
    return dict(audit_id=config['audit_id'],ranking=[dict(code=c,name=names[c],evaluated_windows=counts[c],signed_primary_error_contribution_pp=str(scores[c]*100)) for c in ranking],cases=cases,checked_pdf_amounts=amount_checks,full_archive_checked=raw_dir is not None,original_decision=data['gradual']['decision'],boundary=config['boundary'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('config',type=Path);parser.add_argument('--raw-dir',type=Path);args=parser.parse_args()
    try:
        raw=args.config.read_bytes();out=audit(json.loads(raw),args.raw_dir)
        out['evidence']=dict(source_adapter_sha256=hashlib.sha256(Path(__file__).with_name('tdx_research_source.py').read_bytes()).hexdigest(), config_sha256=digest(raw),code_sha256=digest(Path(__file__).read_bytes()),source_decoder_sha256=digest((ROOT/'valuation/backend/tools/backtest_tdx_history.py').read_bytes()),pypdf_version=pypdf_version)
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,IndexError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
