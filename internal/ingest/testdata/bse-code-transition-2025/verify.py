"""校验原始哈希并通过生产解析器逐字节重建已冻结代码切换CSV。"""
import csv
import hashlib
import io
import json
from pathlib import Path
import runpy

ROOT=Path(__file__).resolve().parent


def rebuild():
    sources=json.loads((ROOT/'sources.json').read_text())
    paths={name:str(ROOT/(name+'.html')) for name in sources}
    for name,path in paths.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==sources[name]['sha256']
    parse=runpy.run_path(str(ROOT.parents[3]/'internal/source/bse/parse.py'))['parse']
    snapshot=parse(paths)
    output=io.StringIO(newline='');writer=csv.writer(output,lineterminator='\n')
    columns=['old_code','new_code','source_name','source_listing_date','switch_date']
    writer.writerow(columns)
    for row in snapshot['transitions']:writer.writerow([row[k] for k in columns])
    return output.getvalue().encode('utf-8')


if __name__=='__main__':
    assert rebuild()==(ROOT/'transitions.csv').read_bytes()
    print('BSE: 248 official code pairs; 6 pilot / 242 later transitions; no identity publication')
