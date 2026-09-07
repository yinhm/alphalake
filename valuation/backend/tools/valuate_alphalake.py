"""从实际 AlphaLake 数据库导出并调用估值 API；不消费 testdata 快照。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('code')
    parser.add_argument('--period',required=True)
    parser.add_argument('--as-of',required=True)
    parser.add_argument('--policy',required=True)
    parser.add_argument('--alphalake',default=str(Path(__file__).resolve().parents[3]/'alphalake'))
    parser.add_argument('--api',default='http://127.0.0.1:8000')
    args = parser.parse_args()
    data = subprocess.check_output([args.alphalake,'export-valuation',args.database,args.code,
        '--period',args.period,'--as-of',args.as_of],text=True)
    payload = json.dumps(dict(data=json.loads(data),policy=json.loads(Path(args.policy).read_text())),allow_nan=False).encode()
    request = Request(args.api.rstrip('/')+'/api/valuation/from-alphalake',data=payload,headers={'Content-Type':'application/json'})
    try:
        with urlopen(request,timeout=120) as response:
            print(response.read().decode())
    except HTTPError as error:
        print(error.read().decode(),file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
