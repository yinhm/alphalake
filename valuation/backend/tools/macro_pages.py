"""从同版DuckDB导出发布中文宏观阅读页，不刷新来源或执行估值。"""
import argparse
import json
from pathlib import Path
import subprocess

from data_sources.macro_pages import publish, read_page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('publish')
    create.add_argument('--database', required=True, type=Path)
    create.add_argument('--id', required=True)
    create.add_argument('--commentary', required=True, type=Path)
    create.add_argument('--alphalake', type=Path, default=Path(__file__).resolve().parents[3] / 'alphalake')
    check = commands.add_parser('validate')
    check.add_argument('--id', required=True)
    compare = commands.add_parser('compare')
    compare.add_argument('--before', required=True)
    compare.add_argument('--after', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'publish':
            if not args.database.is_file():
                raise ValueError('已有DuckDB主库文件必需')
            result = subprocess.run([str(args.alphalake.resolve()), 'export-macro', str(args.database.resolve())],
                                    check=True, capture_output=True, timeout=120)
            page = publish(json.loads(result.stdout), json.loads(args.commentary.read_text(encoding='utf-8')), args.id)
        elif args.command == 'compare':
            from data_sources.macro_comparison import compare
            print(json.dumps(compare(args.before, args.after), ensure_ascii=False))
            return
        else:
            page = read_page(args.id)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(json.dumps(dict(contract=page.contract, id=page.id, url='/macro/' + page.id,
                          content_hash=page.content_hash, series=len(page.snapshot.series),
                          observations=sum(len(s.points) for s in page.snapshot.series)), ensure_ascii=False))


if __name__ == '__main__':
    main()
