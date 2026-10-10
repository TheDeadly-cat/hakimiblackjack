"""Build the reviewed source installer kit; never install or publish it."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

KIT = Path(__file__).resolve().parent / 'windows_trial'
FILES = (
    'CLOSEOUT_GUIDE.md', 'INSTALLER_TEST_LOG.txt', 'INSTALL_TRIAL.cmd',
    'PACKAGE.json', 'README_试用版.txt', 'VALIDATION.json', 'install_trial.py',
    'launch_trial.py', 'tests/test_installer_helpers.py', 'trial_common.py',
    'trial_tools.py',
)


def build(destination: Path) -> Path:
    payloads = {name: (KIT / name).read_bytes() for name in FILES}
    package = json.loads(payloads['PACKAGE.json'])
    validation = json.loads(payloads['VALIDATION.json'])
    if validation['package_id'] != package['package_id']:
        raise ValueError('Package and validation identities differ')
    if (validation['tested_count'], validation['passed_count'],
            validation['failed_count'], validation['skipped_count']) != (28, 28, 0, 0):
        raise ValueError('The reviewed helper suite has not passed completely')
    log = payloads['INSTALLER_TEST_LOG.txt']
    if hashlib.sha256(log).hexdigest() != validation['log_sha256']:
        raise ValueError('The frozen test log has changed')
    if not re.search(rb'Ran 28 tests in [\d.]+s\s+OK\s*$', log):
        raise ValueError('The test log has no complete success footer')
    for name, digest in validation['tested_script_sha256'].items():
        if name not in payloads or hashlib.sha256(payloads[name]).hexdigest() != digest:
            raise ValueError(f'Tested script changed: {name}')
    manifest = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n'
                       for name, data in sorted(payloads.items()))
    payloads['CONTENTS_SHA256.txt'] = manifest.encode('utf-8')
    filename = 'Hakimi_Blackjack_T1R2_Trial_Online_Setup.zip'
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / filename
    checksum = archive.with_suffix('.sha256')
    if archive.exists() or checksum.exists():
        raise FileExistsError('Refusing to overwrite an existing archive or checksum')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name, data in sorted(payloads.items()):
            item = zipfile.ZipInfo('Hakimi_Blackjack_T1R2_Trial/' + name,
                                   date_time=(2026, 10, 10, 0, 0, 0))
            item.create_system = 3
            item.external_attr = 0o100644 << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(item, data, compresslevel=9)
    with zipfile.ZipFile(archive) as zf:
        if zf.testzip() is not None:
            raise ValueError('Built ZIP failed CRC verification')
        for name, data in payloads.items():
            if zf.read('Hakimi_Blackjack_T1R2_Trial/' + name) != data:
                raise ValueError('Built ZIP differs from the reviewed files')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with checksum.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(f'{digest}  {filename}\n')
    print(json.dumps({'archive': str(archive), 'sha256': digest,
                      'package_id': package['package_id'],
                      'source_commit': package['source_commit'],
                      'files': len(payloads)}, ensure_ascii=True))
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)
