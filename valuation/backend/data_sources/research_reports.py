"""读取workspace内已登记的研究交付；不访问财务库或估值引擎。"""
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import unicodedata
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .paths import workspace_path

CONTRACT = 'alphalake-research-v1'
IDENTIFIER = r'^[a-z0-9][a-z0-9-]{0,79}$'
KINDS = ('initial_coverage', 'thesis', 'independent_dcf', 'narrative_batch', 'research_note')
MEDIA = {'.md': 'text/plain', '.txt': 'text/plain', '.json': 'application/json', '.csv': 'text/csv',
         '.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
         '.webp': 'image/webp', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
         '.xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12', '.zip': 'application/zip', '.gz': 'application/gzip'}
MAX_MANIFEST = 256 * 1024
MAX_DOCUMENT = 2 * 1024 * 1024


class ResearchError(ValueError):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code, self.code = status_code, code


class ResearchFile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=IDENTIFIER)
    label: str = Field(min_length=1, max_length=160)
    path: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

    @field_validator('path')
    @classmethod
    def relative_file(cls, value):
        path = PurePosixPath(value)
        if (path.is_absolute() or '..' in path.parts or '\\' in value or ':' in value
                or '\x00' in value or str(path) != value or path.suffix.lower() not in MEDIA):
            raise ValueError('normalized workspace-relative supported file required')
        return value


class ResearchReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    contract: Literal['alphalake-research-v1']
    id: str = Field(pattern=IDENTIFIER)
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=2000)
    kind: Literal['initial_coverage', 'thesis', 'independent_dcf', 'narrative_batch', 'research_note']
    status: Literal['complete', 'partial', 'archived'] = 'complete'
    tickers: list[str] = Field(default_factory=list, max_length=1000)
    published_at: AwareDatetime
    research_date: date | None = None
    financial_period: date | None = None
    information_as_of: AwareDatetime | None = None
    share_date: date | None = None
    run_ids: list[str] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    previous_id: str | None = Field(default=None, pattern=IDENTIFIER)
    report_file: str = Field(default='report', pattern=IDENTIFIER)
    files: list[ResearchFile] = Field(min_length=1, max_length=256)

    @model_validator(mode='after')
    def document_registered(self):
        files = {item.id: item for item in self.files}
        if len(files) != len(self.files) or len({item.path for item in self.files}) != len(self.files):
            raise ValueError('unique file IDs and paths required')
        if self.report_file not in files or not files[self.report_file].path.endswith('.md'):
            raise ValueError('registered Markdown report required')
        if not files[self.report_file].path.startswith('research/' + self.id + '/'):
            raise ValueError('main report belongs in its canonical research directory')
        return self


def file_digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def file_path(value):
    root = workspace_path().resolve()
    try:
        path = (root / value).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ResearchError(404, 'file_missing', '登记的研究文件不存在') from exc
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in MEDIA:
        raise ResearchError(422, 'unsafe_file', '研究文件路径或格式不允许')
    return path


def read_report(identifier):
    if not re.fullmatch(IDENTIFIER, identifier):
        raise ResearchError(404, 'report_missing', '研究报告不存在')
    folder = workspace_path('research', identifier)
    try:
        if (workspace_path('research').is_symlink() or folder.is_symlink()
                or not folder.resolve().is_relative_to(workspace_path().resolve())):
            raise ResearchError(422, 'invalid_report', '研究目录不能使用别名')
    except (OSError, RuntimeError) as exc:
        raise ResearchError(422, 'invalid_report', '研究目录无法解析') from exc
    path = folder / 'research.json'
    try:
        if path.stat().st_size > MAX_MANIFEST or path.is_symlink():
            raise ResearchError(422, 'invalid_report', '研究登记文件不合格')
        body = ResearchReport.model_validate_json(path.read_bytes())
    except FileNotFoundError as exc:
        raise ResearchError(404, 'report_missing', '研究报告不存在') from exc
    except (OSError, ValidationError) as exc:
        raise ResearchError(422, 'invalid_report', '研究登记字段或内容无效') from exc
    if body.id != identifier:
        raise ResearchError(422, 'invalid_report', '研究ID与目录不一致')
    return body


def catalog(query='', kind=None, offset=0, limit=20):
    reports, issues = [], []
    root = workspace_path('research')
    needle = unicodedata.normalize('NFKC', query).casefold().strip()
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ResearchError(422, 'invalid_directory', '研究根目录必须是workspace内的实际目录')
    if root.exists():
        for path in sorted(root.glob('*/research.json')):
            identifier = path.parent.name
            try:
                report = read_report(identifier)
            except ResearchError as exc:
                issues.append(dict(id=identifier, code=exc.code, message=str(exc)))
                continue
            fields = [report.title, report.summary, *report.tickers]
            if kind and kind != report.kind:
                continue
            if needle and needle not in unicodedata.normalize('NFKC', ' '.join(fields)).casefold():
                continue
            reports.append(report)
    reports.sort(key=lambda r: (r.research_date or date.min, r.published_at, r.id), reverse=True)
    return dict(contract=CONTRACT, total=len(reports), offset=offset, limit=limit, issues=issues,
                reports=[r.model_dump(mode='json', exclude={'files'}) for r in reports[offset:offset + limit]])


def registered_file(report, identifier):
    item = next((f for f in report.files if f.id == identifier), None)
    if item is None:
        raise ResearchError(404, 'file_unregistered', '文件未登记到此研究')
    return item


def verified_file(report, identifier):
    item = registered_file(report, identifier)
    path = file_path(item.path)
    try:
        if file_digest(path) != item.sha256:
            raise ResearchError(409, 'file_changed', '研究文件已变化，请核对登记版本')
    except OSError as exc:
        raise ResearchError(404, 'file_missing', '登记的研究文件无法读取') from exc
    return item, path


def report_detail(identifier, document=None):
    report = read_report(identifier)
    item = registered_file(report, document or report.report_file)
    if not item.path.endswith('.md'):
        raise ResearchError(422, 'not_document', '所选文件不是Markdown文档')
    path = file_path(item.path)
    try:
        with path.open('rb') as stream:
            content = stream.read(MAX_DOCUMENT + 1)
        if len(content) > MAX_DOCUMENT:
            raise ResearchError(422, 'document_too_large', '研究正文超过阅读大小限制')
        if hashlib.sha256(content).hexdigest() != item.sha256:
            raise ResearchError(409, 'file_changed', '研究正文已变化，请核对登记版本')
        markdown = content.decode('utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        raise ResearchError(422, 'invalid_document', '研究正文无法按UTF-8读取') from exc
    files = []
    for entry in report.files:
        try:
            size = file_path(entry.path).stat().st_size
        except (ResearchError, OSError):
            size = None
        files.append(dict(**entry.model_dump(), available=size is not None, size_bytes=size,
                          media_type=MEDIA[PurePosixPath(entry.path).suffix.lower()],
                          url=f'/api/research/{report.id}/files/{entry.id}'))
    return dict(report=report.model_dump(mode='json', exclude={'files'}), files=files,
                document=dict(id=item.id, label=item.label, path=item.path, body_md=markdown))


def publish(directory, metadata):
    if directory.is_symlink() or workspace_path('research').is_symlink():
        raise ValueError('canonical research directory required')
    directory = directory.resolve(strict=True)
    expected = workspace_path('research', directory.name).resolve()
    if directory != expected or directory.is_symlink():
        raise ValueError('publish into workspace/research/<id>')
    target = directory / 'research.json'
    if target.exists():
        raise ValueError('research ID already published; use a new version ID')
    body = json.loads(json.dumps(metadata))
    body.setdefault('published_at', datetime.now(timezone.utc).isoformat())
    for item in body.get('files', []):
        # Validate paths before reading or hashing caller-supplied files.
        checked = ResearchFile.model_validate({**item, 'sha256': item.get('sha256', '0' * 64)})
        actual = file_digest(file_path(checked.path))
        if item.get('sha256') and item['sha256'] != actual:
            raise ValueError('supplied file hash mismatch: ' + checked.id)
        item['sha256'] = actual
    report = ResearchReport.model_validate(body)
    if report.id != directory.name:
        raise ValueError('research ID differs from directory')
    for item in report.files:
        if item.path.endswith('.md'):
            path = file_path(item.path)
            if path.stat().st_size > MAX_DOCUMENT:
                raise ValueError('Markdown document exceeds reading limit')
            path.read_text(encoding='utf-8')
    encoded = report.model_dump_json(indent=2) + '\n'
    if len(encoded.encode('utf-8')) > MAX_MANIFEST:
        raise ValueError('research registration exceeds size limit')
    pending = directory / '.research.pending.json'
    with pending.open('x', encoding='utf-8') as stream:
        stream.write(encoded)
    try:
        target.hardlink_to(pending)
    finally:
        pending.unlink()
    return report
