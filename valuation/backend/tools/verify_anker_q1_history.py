"""安克2024Q1收入：原始合并报表→TDX单季位值及公告日期。"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import re
import struct
import zipfile

from pypdf import PdfReader


def verify(directory, full_archive=None):
    report, = json.loads((directory/'reports.json').read_bytes())
    package, = json.loads((directory/'packages.json').read_bytes())
    evidence = json.loads((directory/'evidence.json').read_bytes())
    def checked(name, digest):
        raw = (directory/name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('evidence hash differs: '+name)
        return raw
    request = json.loads((directory/'catalogue-request.json').read_bytes())
    announcement, = json.loads(checked('catalogue.json', request['sha256']))['announcements']
    if (report['code'], report['period'], report['announcement_id'], announcement['secCode'], announcement['orgId'], announcement['announcementTitle']) != ('300866','2024-03-31','1219865739','300866','gfbj0839473','2024年一季度报告') or report['announcement_id'] != announcement['announcementId'] or report['url'] != 'https://static.cninfo.com.cn/'+announcement['adjunctUrl']:
        raise ValueError('announcement identity differs')
    checked(report['file'], report['sha256'])
    page = re.sub(r'\s+', '', PdfReader(directory/report['file']).pages[10].extract_text())
    if any(s not in page for s in ('安克创新科技股份有限公司2024年第一季度报告','2、合并利润表单位：元项目本期发生额上期发生额')):
        raise ValueError('statement scope differs')
    matches = re.findall(r'其中：营业收入([\d,]+\.\d{2})([\d,]+\.\d{2})', page.split('2、合并利润表', 1)[1])
    if len(matches) != 1 or [s.replace(',', '') for s in matches[0]] != evidence['revenue_current_prior_cny']:
        raise ValueError('PDF revenue differs')
    if package['file'] != 'gpcw20240331.zip':
        raise ValueError('package identity differs')
    checked(package['file'], package['sample_sha256'])
    with zipfile.ZipFile(directory/package['file']) as z:
        dat = z.read('gpcw20240331.dat')
    if len(dat) != 2367 or struct.unpack_from('<I', dat, 2)[0] != 20240331 or struct.unpack_from('<H', dat, 6)[0] != 1 or struct.unpack_from('<I', dat, 12)[0] != 2336 or dat[20:26] != b'300866' or struct.unpack_from('<I', dat, 27)[0] != 31:
        raise ValueError('TDX layout differs')
    record = dat[31:]
    if hashlib.sha256(record).hexdigest() != package['record_sha256']:
        raise ValueError('record hash differs')
    bits = struct.unpack_from('<I', record, 229*4)[0]
    if bits != evidence['source_bits'] or bits != struct.unpack('<I', struct.pack('<f', float(Decimal(matches[0][0].replace(',', '')))))[0]:
        raise ValueError('TDX revenue bits differ')
    published = datetime.fromtimestamp(announcement['announcementTime']/1000, timezone(timedelta(hours=8))).date()
    if struct.unpack_from('<f', record, 313*4)[0] != int(published.strftime('%y%m%d')):
        raise ValueError('FN314 publication differs')
    if full_archive:
        raw = full_archive.read_bytes()
        if hashlib.sha256(raw).hexdigest() != package['sha256'] or hashlib.md5(raw).hexdigest() != package['md5']:
            raise ValueError('full archive hash differs')
        with zipfile.ZipFile(full_archive) as z:
            full = z.read(z.namelist()[0])
        found = []
        for i in range(struct.unpack_from('<H', full, 6)[0]):
            code, marker, offset = struct.unpack_from('<6sBI', full, 20+11*i)
            if code == b'300866':
                found.append(full[offset:offset+2336])
        if found != [record]:
            raise ValueError('whole record differs')
    return dict(code='300866', period='2024-03-31', source_bits=bits, source_value_cny=struct.unpack('<f', struct.pack('<I', bits))[0], pdf_current_prior_cny=evidence['revenue_current_prior_cny'], available_from=(published+timedelta(days=1)).isoformat()+'T00:00:00+08:00', boundary='FN230 current Q1 verified; comparative printed amount is not a TDX fact; later acquisition is not strict historical PIT')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--full-archive', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory, args.full_archive), ensure_ascii=False, indent=2))
