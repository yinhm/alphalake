"""巨潮PDF唯一原文、按官方URL引用及可跨语言复用的下载收据。"""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from data_sources.paths import workspace_path

CONTRACT = 'cninfo-pdf-cache-v1'


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def document_path(url):
    match = re.fullmatch(r'https://static\.cninfo\.com\.cn/finalpage/(\d{4}-\d{2}-\d{2})/(\d+\.(?i:pdf))', url)
    if not match:
        raise ValueError('official CNINFO document URL required')
    date.fromisoformat(match[1])
    return workspace_path('cninfo', 'documents', match[1], match[2])


@contextmanager
def document_lock(url):
    view = document_path(url)
    lock = workspace_path('derived', 'pdf-cache', 'locks', view.parent.name, view.name + '.lock')
    if not lock.resolve().is_relative_to(workspace_path().resolve()):
        raise ValueError('PDF lock leaves workspace')
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def verified_pdf(path, sha256):
    path = Path(path).resolve(strict=True)
    if not path.is_relative_to(workspace_path().resolve()):
        raise ValueError('PDF cache leaves workspace')
    if not path.is_file() or path.stat().st_size > 256 << 20:
        raise ValueError('source response is not a bounded PDF')
    if not re.fullmatch(r'[a-f0-9]{64}', sha256) or digest(path) != sha256:
        raise ValueError('archived PDF hash differs; corrupt cache rejected')
    with path.open('rb') as stream:
        if not stream.read(512).lstrip().startswith(b'%PDF-'):
            raise ValueError('source response is not a bounded PDF')
    return path


def canonical_pdf(path, sha256):
    path = verified_pdf(path, sha256)
    canonical = workspace_path('cninfo', 'objects', sha256[:2], sha256 + '.pdf')
    if not canonical.parent.resolve().is_relative_to(workspace_path().resolve()):
        raise ValueError('PDF archive leaves workspace')
    canonical.parent.mkdir(parents=True, exist_ok=True)
    if canonical.exists():
        verified_pdf(canonical, sha256)
    else:
        try:
            os.link(path, canonical)
        except FileExistsError:
            verified_pdf(canonical, sha256)
    return canonical


def publish_reference(url, path, sha256, fetched_at=None):
    """调用方持有同URL锁；首次下载时间未知时保持空，不借用文件时间。"""
    canonical = canonical_pdf(path, sha256)
    view = document_path(url)
    receipt_file = view.with_name(view.name + '.json')
    if not view.parent.resolve().is_relative_to(workspace_path().resolve()):
        raise ValueError('PDF reference directory leaves workspace')
    if fetched_at is not None:
        fetched = datetime.fromisoformat(fetched_at)
        if fetched.tzinfo is None or fetched > datetime.now(timezone.utc):
            raise ValueError('valid original PDF retrieval time required')
    if receipt_file.exists():
        previous = read_reference(url)
        if (previous['sha256'] == sha256 and previous['fetched_at'] is not None and
                (fetched_at is None or datetime.fromisoformat(previous['fetched_at']) < datetime.fromisoformat(fetched_at))):
            fetched_at = previous['fetched_at']
    record = dict(contract=CONTRACT, url=url, sha256=sha256,
                  local_path=str(canonical.relative_to(workspace_path())), fetched_at=fetched_at)
    view.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pdf-reference-', dir=view.parent) as pending:
        pending = Path(pending)
        (pending/'reference.pdf').symlink_to(os.path.relpath(canonical, view.parent))
        with (pending/'receipt.json').open('w') as stream:
            stream.write(json.dumps(record, ensure_ascii=False, indent=2)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending/'reference.pdf', view)
        os.replace(pending/'receipt.json', receipt_file)
    return record


def read_reference(url, expected_sha256=None):
    view = document_path(url)
    receipt_file = view.with_name(view.name + '.json')
    if not receipt_file.resolve().is_relative_to(workspace_path().resolve()):
        raise ValueError('PDF receipt leaves workspace')
    if not receipt_file.exists():
        if view.exists() or view.is_symlink():
            raise ValueError('PDF reference has no provenance receipt')
        return None
    if receipt_file.stat().st_size > 16384:
        raise ValueError('PDF receipt exceeds size limit')
    record = json.loads(receipt_file.read_text())
    if set(record) != {'contract', 'url', 'sha256', 'local_path', 'fetched_at'} or record['contract'] != CONTRACT or record['url'] != url:
        raise ValueError('PDF cache provenance differs from official URL')
    sha = record['sha256']
    if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha):
        raise ValueError('invalid PDF cache hash')
    if expected_sha256 is not None and sha != expected_sha256:
        raise ValueError('PDF cache differs from expected source revision')
    canonical = workspace_path('cninfo', 'objects', sha[:2], sha + '.pdf')
    if record['local_path'] != str(canonical.relative_to(workspace_path())):
        raise ValueError('PDF cache path is not the canonical source object')
    verified_pdf(canonical, sha)
    if not view.is_symlink() or view.resolve(strict=True) != canonical.resolve(strict=True):
        raise ValueError('PDF directory reference differs from canonical object')
    if record['fetched_at'] is not None:
        fetched = datetime.fromisoformat(record['fetched_at'])
        if fetched.tzinfo is None or fetched > datetime.now(timezone.utc):
            raise ValueError('invalid original PDF retrieval time')
    return record
