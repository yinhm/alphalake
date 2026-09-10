"""从通过独立复验的冻结研究生成显式一年期校准政策，不发布财务事实。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from data_sources.alphalake_calibration import FirstYearCalibration
from tools.backtest_tdx_origins import fit_calibration, study


def prepare(protocol_raw,source_raw,validation_raw,period,codes,prepared_at):
    p=json.loads(protocol_raw);source=json.loads(source_raw);saved=json.loads(validation_raw)
    digest=lambda b:hashlib.sha256(b).hexdigest()
    if source['study_sha256']!=digest(protocol_raw) or saved['evidence']['protocol_sha256']!=digest(protocol_raw) or saved['evidence']['snapshot_sha256']!=digest(source_raw):
        raise ValueError('calibration source or protocol hash differs')
    if p.get('fixed_validation_model')!='bias_half' or p['calibration']['method']!='previous_origin_development_weighted_ebit_scale':
        raise ValueError('fixed validated multiplicative model required')
    # 核验本地冻结结果，不信任JSON里单独一个passed标记；不选择新的候选。
    dev=study(p,source,'development');validated=study(p,source,'holdout',dev)
    if validated['validation']!=saved['validation'] or validated['summary']!=saved['summary'] or not validated['validation']['verdict']['passed']:
        raise ValueError('independent validation does not reproduce or pass')
    if (p['calibration']['minimum_ebit_scale'],p['calibration']['maximum_ebit_scale'])!=(.5,1.5):
        raise ValueError('unsupported fitted scale bounds')
    fit=fit_calibration(p,source,period)
    if fit['status']!='fitted':raise ValueError('insufficient past calibration pairs')
    rows=[dict(code=r['code'],predicted_ebit=r['forecasts']['current_rule']['ebit'],actual_ebit=r['actual']['ebit'],actual_revenue=r['actual']['revenue'])
          for r in fit['results'] if r['status']=='evaluated']
    return FirstYearCalibration(model_id='lagged-weighted-ebit-scale-half-v1',forecast_year=1,
        report_period=period,training_origin=fit['origin'],training_as_of=fit['evaluation_as_of'],prepared_at=prepared_at,
        approved_codes=codes,source_snapshot_sha256=digest(source_raw),validation_protocol_sha256=digest(protocol_raw),
        validation_result_sha256=digest(validation_raw),evidence_basis='research_tdx_fn314_not_strict_pit',amount_unit='million_CNY',
        observations=rows,raw_scale=fit['weighted_ebit_scale'],scale=fit['ebit_scale'],weight=.5,
        review_note='独立复验支持一年期合并调整利润代理校准；仅限显式指定公司与报告期。源层简化回溯及当前名单抽样，非严格PIT；第2至10年、再投资和终值无本研究验证。')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('protocol',type=Path);p.add_argument('snapshot',type=Path);p.add_argument('validation',type=Path)
    p.add_argument('--period',required=True);p.add_argument('--code',action='append',required=True)
    a=p.parse_args()
    try:
        output=prepare(a.protocol.read_bytes(),a.snapshot.read_bytes(),a.validation.read_bytes(),a.period,a.code,datetime.now(timezone.utc))
        print(output.model_dump_json(indent=2))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
