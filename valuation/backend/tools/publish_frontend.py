"""发布前端候选：保留已发布散列资源，最后原子切换HTML。"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

from data_sources.paths import workspace_path

DESTINATION = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def atomic_write(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.pending-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class EntryAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('src', 'href') and value and value.startswith('/assets/'):
                self.assets.add(value[1:])


def candidate_files(candidate):
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError('候选必须为实际构建目录')
    candidate = candidate.resolve(strict=True)
    if (candidate / 'assets').is_symlink():
        raise ValueError('候选资源目录不允许软链接')
    manifest_path = candidate / '.vite' / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError('候选缺少Vite构建清单')
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict) or 'index.html' not in manifest:
        raise ValueError('候选缺少HTML入口清单')
    expected = set()
    for entry in manifest.values():
        expected.add(entry['file'])
        expected.update(entry.get('css', []))
        expected.update(entry.get('assets', []))
        for key in (*entry.get('imports', []), *entry.get('dynamicImports', [])):
            if key not in manifest:
                raise ValueError('候选动态依赖未登记')
    files = {}
    for path in (candidate / 'assets').rglob('*'):
        if path.is_symlink():
            raise ValueError('候选资源不允许软链接')
        if not path.is_file():
            continue
        relative = path.relative_to(candidate).as_posix()
        if not re.fullmatch(r'.+-[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9]+)+', path.name):
            raise ValueError('只发布带内容标识的构建资源')
        files[relative] = path
    for name in expected:
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or not name.startswith('assets/') or name not in files:
            raise ValueError('候选清单资源缺失或路径无效：' + name)
    index = candidate / 'index.html'
    if index.is_symlink() or not index.is_file():
        raise ValueError('候选HTML入口缺失')
    content = index.read_bytes()
    parser = EntryAssets()
    parser.feed(content.decode('utf-8'))
    if (manifest['index.html']['file'] not in parser.assets
            or not parser.assets <= files.keys()):
        raise ValueError('HTML与构建清单不匹配')
    return files, content, digest(manifest_path)


def publish(candidate, destination=DESTINATION, receipt_dir=None):
    files, content, manifest_hash = candidate_files(candidate)
    if destination.is_symlink() or (destination / 'assets').is_symlink() or destination.resolve() == candidate.resolve():
        raise ValueError('发布目标不允许软链接或与候选相同')
    receipt_dir = receipt_dir or workspace_path('derived', 'frontend-publication')
    receipt_dir.mkdir(parents=True, exist_ok=True)
    with (receipt_dir / 'publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # 散列同名内容必须相同，不能改写已有页面所依赖的文件。
        for name, path in files.items():
            target = destination / name
            if target.is_symlink() or (target.exists() and digest(path) != digest(target)):
                raise ValueError('已发布资源同名内容冲突：' + name)
        destination.mkdir(parents=True, exist_ok=True)
        added = []
        for name, path in files.items():
            target = destination / name
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.pending-', delete=False) as stream:
                pending = Path(stream.name)
                try:
                    with path.open('rb') as source:
                        shutil.copyfileobj(source, stream, 1024 * 1024)
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.link(pending, target)
                finally:
                    pending.unlink(missing_ok=True)
            added.append(name)
        index = destination / 'index.html'
        previous = index.read_bytes() if index.exists() else None
        receipt = dict(status='unchanged' if previous == content else 'published',
            index_sha256=hashlib.sha256(content).hexdigest(), manifest_sha256=manifest_hash,
            previous_index_sha256=hashlib.sha256(previous).hexdigest() if previous else None,
            added_files=added, added_bytes=sum(files[name].stat().st_size for name in added),
            candidate_asset_count=len(files), published_at=datetime.now(timezone.utc).isoformat())
        if previous is not None and previous != content:
            backup = receipt_dir / 'previous-index.html'
            old_backup = backup.read_bytes() if backup.exists() else None
            atomic_write(receipt_dir / 'previous-index.html', previous)
        try:
            if previous != content:
                atomic_write(index, content)
            atomic_write(receipt_dir / 'current.json', (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode())
        except BaseException:
            if previous is not None:
                atomic_write(index, previous)
            else:
                index.unlink(missing_ok=True)
            if previous is not None and previous != content:
                if old_backup is None:
                    backup.unlink(missing_ok=True)
                else:
                    atomic_write(backup, old_backup)
            raise
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, default=workspace_path('derived', 'frontend-candidate'))
    args = parser.parse_args()
    try:
        result = publish(args.candidate)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
