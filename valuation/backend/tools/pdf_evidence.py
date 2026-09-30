"""有界复用原文文本；每次仍校验文件内容，解析器替换时重新提取。"""
from functools import lru_cache
import hashlib
from pathlib import Path
import subprocess


@lru_cache(maxsize=16)
def _extract(path, sha256, extractor):
    return extractor(['pdftotext', '-layout', str(path), '-'], text=True)


def verified_pdf_text(path, sha256):
    path = Path(path).resolve()
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        raise ValueError('PDF hash differs')
    # ponytail: 单进程最多16份文本；不缓存核验结论，也不跨进程持久化。
    return _extract(path, sha256, subprocess.check_output)
