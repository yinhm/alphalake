"""新名单排除旧研究，开发回执在打开留出源之前验证。"""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from tools.validate_tdx_zero_growth import verify_sampling,load_development,score

ROOT=Path(__file__).resolve().parents[3]
DIR=ROOT/'valuation/research/tdx-zero-growth-validation'


def test_zero_growth_development_and_holdout_gate(tmp_path):
    p=json.loads((DIR/'protocol.json').read_text());verify_sampling(p)
    _,_,rows=load_development(p);actual=score(p,rows,'development');recorded=json.loads((DIR/'development-selection.json').read_text())
    assert actual=={k:v for k,v in recorded.items() if k not in ('evidence','results_reference')}
    assert actual['decision']['passed'] and actual['evaluated_companies']==50
    assert actual['summary']['statuses']=={'blocked':98,'evaluated':202}
    bad=copy.deepcopy(p);bad['samples'][-1]=bad['samples'][0]|dict(split='holdout')
    with pytest.raises(ValueError,match='sampling differs'):verify_sampling(bad)
    fake=copy.deepcopy(recorded);fake['decision']['passed']=False;selection=tmp_path/'selection.json';selection.write_text(json.dumps(fake))
    args=[sys.executable,'-m','tools.validate_tdx_zero_growth',str(DIR/'protocol.json'),'--phase','holdout','--selection',str(selection),'--snapshot',str(tmp_path/'must-not-open.json')]
    result=subprocess.run(args,cwd=ROOT/'valuation/backend',capture_output=True,text=True)
    assert result.returncode==1 and json.loads(result.stdout)['reason']=='development selection differs'
