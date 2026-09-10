"""显式的一年期利润校准政策；研究估计不冒充标准财务事实。"""
from datetime import date, datetime
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CalibrationObservation(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    code: str=Field(pattern=r'^[0-9]{6}$')
    predicted_ebit: float=Field(gt=0,strict=True)
    actual_ebit: float=Field(strict=True)
    actual_revenue: float=Field(gt=0,strict=True)


def weighted_median(observations):
    pairs=sorted(observations)
    if any(not math.isfinite(x) or not math.isfinite(w) or w<=0 for x,w in pairs):
        raise ValueError('nonfinite calibration ratio or weight')
    total=sum(w for _,w in pairs)
    if not math.isfinite(total):raise ValueError('nonfinite calibration total weight')
    half=total/2;weight=0
    for ratio,w in pairs:
        weight+=w
        if weight>=half:return ratio
    raise ValueError('empty calibration observations')


def fitted_scale(observations):
    return weighted_median((r.actual_ebit/r.predicted_ebit,r.predicted_ebit/r.actual_revenue) for r in observations)


class FirstYearCalibration(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    model_id: Literal['lagged-weighted-ebit-scale-half-v1']
    forecast_year: Literal[1]
    report_period: date
    training_origin: date
    training_as_of: datetime
    prepared_at: datetime
    approved_codes: list[str]=Field(min_length=1)
    source_snapshot_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    validation_protocol_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    validation_result_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    evidence_basis: Literal['research_tdx_fn314_not_strict_pit']
    amount_unit: Literal['million_CNY']
    observations: list[CalibrationObservation]=Field(min_length=30)
    raw_scale: float=Field(strict=True)
    scale: float=Field(ge=.5,le=1.5,strict=True)
    weight: Literal[0.5]
    review_note: str=Field(min_length=1)

    @model_validator(mode='after')
    def validate_evidence(self):
        if (self.report_period.month!=6 or self.report_period.day!=30
                or self.training_origin!=self.report_period.replace(year=self.report_period.year-1)):
            raise ValueError('calibration requires one-year H1 training window')
        if (self.training_as_of.utcoffset() is None or self.prepared_at.utcoffset() is None
                or self.training_as_of.date()<=self.report_period or self.training_as_of>self.prepared_at):
            raise ValueError('invalid calibration availability chronology')
        codes=[r.code for r in self.observations]
        if len(set(codes))!=len(codes):raise ValueError('duplicate training company')
        if len(set(self.approved_codes))!=len(self.approved_codes) or any(len(c)!=6 or not c.isascii() or not c.isdigit() for c in self.approved_codes):
            raise ValueError('invalid approved calibration companies')
        raw=fitted_scale(self.observations)
        if not math.isfinite(raw) or self.raw_scale!=raw or self.scale!=max(.5,min(1.5,raw)):
            raise ValueError('calibration coefficient differs from training observations')
        return self

    @property
    def multiplier(self):
        return (1+self.scale)/2
