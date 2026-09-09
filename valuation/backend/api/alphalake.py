"""AlphaLake 导出包的正式估值入口，原生返回桥接结果并保留可重放输入。"""
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import sys
import json
import os
from pathlib import Path
import tempfile

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from data_sources.alphalake import AlphaLakeRequest, MissingInputs, build_inputs, content_hash
from engine.orchestrator import run_full_valuation

router = APIRouter(prefix='/api/valuation')


def runtime_versions():
    return {'python':sys.version.split()[0],**{name:version(name) for name in ('pydantic','numpy','scipy','fastapi')}}


def engine_revision():
    root = Path(__file__).resolve().parents[1]
    paths = sorted((root/'engine').glob('*.py'))+[root/'data_sources/alphalake.py',root/'data_sources/alphalake_wacc.py',Path(__file__).resolve()]
    digest = hashlib.sha256(json.dumps(runtime_versions(),sort_keys=True).encode())
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    return digest.hexdigest()


# 服务启动时固定实际加载版本；部署更新后应重启进程。
ENGINE_REVISION = engine_revision()


def evaluate(request: AlphaLakeRequest):
    inputs, audit = build_inputs(request)
    revision = ENGINE_REVISION
    request_data = request.model_dump(mode='json')
    run_id = content_hash(dict(request=request_data,engine_revision=revision))
    report = run_full_valuation(inputs)
    result = dict(run_id=run_id,engine_revision=revision,runtime_versions=runtime_versions(),status='illustrative_valuation_completed',
        request=request_data,inputs=inputs.model_dump(mode='json'),audit=audit,
        report=jsonable_encoder(asdict(report)))
    # 保存输入/政策/引擎版本与输出。同内容重放不覆盖；失败不产生成功记录。
    root = Path(os.environ.get('ALPHALAKE_VALUATION_RUN_DIR',str(Path(__file__).resolve().parents[1]/'data/alphalake_runs')))
    root.mkdir(parents=True,exist_ok=True)
    target = root/(run_id+'.json')
    payload = json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n'
    if target.exists():
        if target.read_text() != payload:
            raise ValueError('stored valuation differs for identical inputs and engine version')
    else:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=root,delete=False) as f:
            temporary = Path(f.name)
            try:
                f.write(payload);f.flush();os.fsync(f.fileno())
                # 原子创建：并发同内容运行不会部分覆盖文件。
                try:
                    os.link(temporary,target)
                except FileExistsError:
                    if target.read_text() != payload:
                        raise ValueError('concurrent valuation differs')
            finally:
                temporary.unlink(missing_ok=True)
    return result


@router.post('/from-alphalake')
def from_alphalake(request: AlphaLakeRequest):
    try:
        return evaluate(request)
    except MissingInputs as error:
        raise HTTPException(status_code=422,detail=dict(status='blocked_missing_inputs',missing=error.items)) from error
    except (ValueError,KeyError,TypeError,ArithmeticError) as error:
        raise HTTPException(status_code=422,detail=dict(status='rejected_input_or_policy',reason=str(error))) from error
