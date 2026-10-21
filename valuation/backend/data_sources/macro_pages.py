"""宏观消费快照及不可变期次；请求侧不连接主库或启动采集。"""
from datetime import date, datetime
from decimal import Decimal
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from data_sources.paths import workspace_path

IDENTIFIER = r'^[a-z0-9][a-z0-9-]{1,100}$'
CODE = r'^cn_[a-z0-9_]{2,100}$'
DIGEST = r'^[0-9a-f]{64}$'
MAX_BYTES = 16 * 1024 * 1024


class MacroPageError(ValueError):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Point(Model):
    period_start: date
    period_end: date
    value: Decimal | None
    value_status: Literal['reported', 'not_reported']
    release_id: int = Field(gt=0)
    source_locator: str = Field(min_length=1, max_length=300)


class Series(Model):
    series_code: str = Field(pattern=CODE)
    name_zh: str = Field(min_length=1, max_length=120)
    source: Literal['nbs', 'pbc', 'mof']
    dataset: str = Field(pattern=r'^macro_[a-z_]+_v[0-9]+$')
    geography: Literal['CN']
    frequency: Literal['monthly', 'quarterly']
    period_basis: Literal['month', 'month_or_jan_feb', 'quarter', 'ytd', 'point_in_time']
    unit: Literal['CNY', 'fraction', 'index_points', 'percentage_points', 'square_metre', 'CNY_per_person']
    price_basis: Literal['current_prices', 'constant_prices', 'price_change', 'not_applicable']
    seasonal_adjustment: Literal['seasonally_adjusted', 'not_seasonally_adjusted', 'provider_unspecified']
    definition: str = Field(min_length=1, max_length=3000)
    points: list[Point] = Field(max_length=10000)


class Release(Model):
    release_id: int = Field(gt=0)
    artifact_id: int = Field(gt=0)
    source: Literal['nbs', 'pbc', 'mof']
    dataset: str
    url: str
    sha256: str = Field(pattern=DIGEST)
    source_published_at: AwareDatetime | None
    publication_precision: Literal['timestamp', 'unknown']
    first_acquired_at: AwareDatetime
    recorded_at: AwareDatetime

    @model_validator(mode='after')
    def publication(self):
        parts = urlsplit(self.url)
        host, prefix = {'nbs': ('www.stats.gov.cn', '/sj/'),
                        'pbc': ('www.pbc.gov.cn', '/diaochatongjisi/'),
                        'mof': ('gks.mof.gov.cn', '/tongjishuju/')}[self.source]
        if parts.scheme != 'https' or parts.netloc != host or not parts.path.startswith(prefix) or parts.query or parts.fragment:
            raise ValueError('official macro source URL required')
        if (self.source_published_at is None) != (self.publication_precision == 'unknown'):
            raise ValueError('publication precision mismatch')
        if self.source_published_at and self.source_published_at > self.first_acquired_at:
            raise ValueError('publication exceeds acquisition')
        return self


class Snapshot(Model):
    contract: Literal['alphalake-macro-snapshot-v1']
    exported_at: AwareDatetime
    vintage: Literal['latest_source_vintage_not_historical_pit']
    series: list[Series] = Field(min_length=1, max_length=2000)
    releases: list[Release] = Field(min_length=1, max_length=5000)

    @model_validator(mode='after')
    def relationships(self):
        releases = {r.release_id: r for r in self.releases}
        if len(releases) != len(self.releases) or len({s.series_code for s in self.series}) != len(self.series):
            raise ValueError('duplicate macro identity')
        referenced = set()
        for series in self.series:
            days = [p.period_end for p in series.points]
            if days != sorted(set(days)):
                raise ValueError('macro periods must be unique and ordered')
            for point in series.points:
                release = releases.get(point.release_id)
                if release is None or (release.source, release.dataset) != (series.source, series.dataset):
                    raise ValueError('macro release lineage mismatch')
                if point.period_start > point.period_end or point.period_end > self.exported_at.date():
                    raise ValueError('invalid macro statistical period')
                if (point.value is None) != (point.value_status == 'not_reported'):
                    raise ValueError('missing macro value is not zero')
                referenced.add(release.release_id)
        if referenced != set(releases):
            raise ValueError('unreferenced macro release')
        if any(r.first_acquired_at > self.exported_at or r.recorded_at > self.exported_at for r in self.releases):
            raise ValueError('source exceeds export time')
        return self


class Evidence(Model):
    series_code: str = Field(pattern=CODE)
    period_end: date
    value: Decimal | None


class Section(Model):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=10000)
    evidence: list[Evidence] = Field(min_length=1, max_length=50)


class Commentary(Model):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1500)
    author: str = Field(min_length=1, max_length=100)
    reviewed_at: AwareDatetime
    sections: list[Section] = Field(min_length=1, max_length=30)
    limitations: list[str] = Field(min_length=1, max_length=20)


class Page(Model):
    contract: Literal['alphalake-macro-page-v1'] = 'alphalake-macro-page-v1'
    id: str = Field(pattern=IDENTIFIER)
    published_at: AwareDatetime
    previous_id: str | None = Field(default=None, pattern=IDENTIFIER)
    content_hash: str = Field(pattern=DIGEST)
    snapshot: Snapshot
    commentary: Commentary

    @model_validator(mode='after')
    def binding(self):
        if self.snapshot.exported_at > self.published_at or self.commentary.reviewed_at > self.published_at:
            raise ValueError('future macro publication input')
        points = {(s.series_code, p.period_end): p.value for s in self.snapshot.series for p in s.points}
        for section in self.commentary.sections:
            for proof in section.evidence:
                key = proof.series_code, proof.period_end
                if key not in points or points[key] != proof.value:
                    raise ValueError('commentary evidence differs from snapshot')
        if self.content_hash != content_hash(self.snapshot, self.commentary):
            raise ValueError('macro content signature differs')
        return self


class Entry(Model):
    id: str = Field(pattern=IDENTIFIER)
    title: str
    summary: str
    published_at: AwareDatetime
    exported_at: AwareDatetime
    content_hash: str = Field(pattern=DIGEST)
    sha256: str = Field(pattern=DIGEST)
    series_count: int = Field(gt=0)
    observation_count: int = Field(gt=0)
    latest_statistical_period: date


class Index(Model):
    contract: Literal['alphalake-macro-index-v1'] = 'alphalake-macro-index-v1'
    current_id: str | None = Field(pattern=IDENTIFIER)
    publications: list[Entry] = Field(max_length=5000)

    @model_validator(mode='after')
    def registration(self):
        ids = [p.id for p in self.publications]
        if len(set(ids)) != len(ids) or (self.current_id != ids[0] if ids else self.current_id is not None):
            raise ValueError('macro index identity mismatch')
        return self


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def content_hash(snapshot, commentary):
    facts = snapshot.model_dump(mode='json', exclude={'exported_at'})
    return hashlib.sha256(encoded([facts, commentary.model_dump(mode='json')])).hexdigest()


def root_path():
    root = workspace_path('macro').resolve()
    if not root.is_relative_to(workspace_path().resolve()):
        raise MacroPageError(422, '宏观发布目录越界')
    return root


def read_json(path):
    root = root_path()
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise MacroPageError(404, '宏观发布文件不存在或路径越界')
    try:
        if resolved.stat().st_size > MAX_BYTES:
            raise MacroPageError(422, '宏观发布文件超出限制')
        raw = resolved.read_bytes()
        return json.loads(raw), raw
    except (ValueError, UnicodeError, OSError) as exc:
        raise MacroPageError(422, '宏观发布文件无效') from exc


def catalog():
    path = root_path() / 'index.json'
    if not path.exists() and not path.is_symlink():
        return {'contract': 'alphalake-macro-index-v1', 'current_id': None, 'publications': []}
    value, _ = read_json(path)
    try:
        return Index.model_validate(value).model_dump(mode='json')
    except ValueError as exc:
        raise MacroPageError(422, '宏观发布目录无效') from exc


def read_page(identifier):
    if not re.fullmatch(IDENTIFIER, identifier):
        raise MacroPageError(404, '宏观期次不存在')
    entry = next((p for p in catalog()['publications'] if p['id'] == identifier), None)
    if entry is None:
        raise MacroPageError(404, '宏观期次不存在')
    value, raw = read_json(workspace_path('macro', 'publications', identifier + '.json'))
    if hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise MacroPageError(409, '宏观发布哈希不匹配')
    try:
        page = Page.model_validate(value)
    except ValueError as exc:
        raise MacroPageError(422, '宏观发布数据或点评绑定无效') from exc
    if (page.id != identifier or page.content_hash != entry.get('content_hash')
            or page.commentary.title != entry['title'] or page.commentary.summary != entry['summary']
            or page.published_at != datetime.fromisoformat(entry['published_at'])
            or page.snapshot.exported_at != datetime.fromisoformat(entry['exported_at'])
            or len(page.snapshot.series) != entry['series_count']
            or sum(len(s.points) for s in page.snapshot.series) != entry['observation_count']
            or max(p.period_end for s in page.snapshot.series for p in s.points).isoformat() != entry['latest_statistical_period']):
        raise MacroPageError(409, '宏观发布与目录不一致')
    return page


def publish(snapshot, commentary, identifier):
    from datetime import datetime, timezone
    if identifier == 'history' or not re.fullmatch(IDENTIFIER, identifier):
        raise ValueError('宏观期次ID无效')
    snapshot, commentary = Snapshot.model_validate(snapshot), Commentary.model_validate(commentary)
    folder = root_path()
    folder.mkdir(parents=True, exist_ok=True)
    work = workspace_path('derived', 'macro-pages').resolve()
    if not work.is_relative_to(workspace_path().resolve()):
        raise ValueError('宏观发布工作目录越界')
    work.mkdir(parents=True, exist_ok=True)
    with (work / 'publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        index = catalog()
        digest = content_hash(snapshot, commentary)
        for entry in index['publications']:
            if entry['id'] == identifier and entry['content_hash'] != digest:
                raise ValueError('宏观期次已发布，不允许覆盖；请使用新ID')
            if entry['content_hash'] == digest:
                return read_page(entry['id'])
        now = datetime.now(timezone.utc)
        page = Page(id=identifier, published_at=now, previous_id=index['current_id'], content_hash=digest,
                    snapshot=snapshot, commentary=commentary)
        publications = folder / 'publications'
        publications.mkdir(exist_ok=True)
        path = publications / (page.id + '.json')
        if not publications.resolve().is_relative_to(folder.resolve()):
            raise ValueError('宏观发布目录越界')
        raw = encoded(page.model_dump(mode='json'))
        if len(raw) > MAX_BYTES:
            raise ValueError('宏观发布文件超出限制')
        with path.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        entry = dict(id=page.id, title=commentary.title, summary=commentary.summary,
                     published_at=now.isoformat(), exported_at=snapshot.exported_at.isoformat(),
                     content_hash=digest, sha256=hashlib.sha256(raw).hexdigest(),
                     series_count=len(snapshot.series), observation_count=sum(len(s.points) for s in snapshot.series),
                     latest_statistical_period=max(p.period_end for s in snapshot.series for p in s.points).isoformat())
        index['current_id'] = page.id
        index['publications'].insert(0, entry)
        pending = work / 'index.pending.json'
        try:
            with pending.open('xb') as stream:
                stream.write(encoded(Index.model_validate(index).model_dump(mode='json')))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, folder / 'index.json')
        except BaseException:
            path.unlink(missing_ok=True)
            pending.unlink(missing_ok=True)
            raise
        return page
