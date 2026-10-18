"""验证并登记workspace/research中的正式交付，不执行估值或复制源PDF。"""
import argparse
import json
from pathlib import Path

from data_sources.research_reports import publish, read_report, verified_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('publish')
    create.add_argument('directory', type=Path)
    create.add_argument('--metadata', type=Path, required=True)
    validate = commands.add_parser('validate')
    validate.add_argument('directory', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'publish':
            report = publish(args.directory, json.loads(args.metadata.read_text()))
        else:
            from data_sources.paths import workspace_path
            if args.directory.resolve() != workspace_path('research', args.directory.name).resolve():
                raise ValueError('canonical research directory required')
            report = read_report(args.directory.name)
            for entry in report.files:
                verified_file(report, entry.id)
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(json.dumps(dict(contract=report.contract, id=report.id, status='published' if args.command == 'publish'
                          else 'verified', files=len(report.files), url='/research/' + report.id), ensure_ascii=False))


if __name__ == '__main__':
    main()
