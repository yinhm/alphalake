"""动态数据统一存放于 workspace；环境变量可显式指定另一个数据根目录。"""
import os
from pathlib import Path


def workspace_path(*parts):
    root = Path(os.environ.get('ALPHALAKE_WORKSPACE', str(Path(__file__).resolve().parents[3] / 'workspace')))
    return root.joinpath(*parts)
