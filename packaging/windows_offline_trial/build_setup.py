"""Freeze the verified onedir runtime into a separate offline setup executable."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import zipfile

HERE = Path(__file__).resolve().parent
COMMIT = '3ec2006e82c4bdbc8da01bb0e1212b64c9111d2c'
TREE = '7fc1e8bb64c5bc4a783941fee536f394acf1fff7'
PACKAGE_ID = 'T1O1-3ec2006'


def wrapper_identity():
    return dict(commit=subprocess.check_output(['git', '-C', str(HERE.parents[1]),
        'rev-parse', 'HEAD'], text=True).strip(),
        status=subprocess.check_output(['git', '-C', str(HERE.parents[1]),
        'status', '--porcelain'], text=True),
        files={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(HERE.glob('*.py'))})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    output = args.output.resolve()
    if output.exists():
        raise ValueError('保留旧构建；输出必须是新目录')
    if (sys.version_info[:3] != (3, 14, 6)
            or importlib.metadata.version('pyinstaller') != '6.22.3'):
        raise ValueError('安装器构建工具版本不符')
    wrapper_before = wrapper_identity()
    manifest = json.loads((runtime / 'BUNDLE_MANIFEST.json').read_text(encoding='utf-8'))
    if manifest['commit'] != COMMIT or manifest['source_tree'] != TREE or manifest['package_id'] != PACKAGE_ID:
        raise ValueError('运行目录身份不符')
    if manifest.get('wrapper_files') != wrapper_before['files']:
        raise ValueError('运行目录不属于当前固定包装源码')
    output.mkdir(parents=True)
    payload = output / 'payload'
    payload.mkdir()
    paths = sorted(path for path in runtime.rglob('*') if path.is_file())
    files = {}
    archive_path = payload / 'runtime.zip'
    with zipfile.ZipFile(archive_path, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in paths:
            if path.is_symlink() or path.is_junction():
                raise ValueError('运行目录含链接')
            relative = path.relative_to(runtime).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if relative != 'BUNDLE_MANIFEST.json' and manifest['files'].get(relative) != digest:
                raise ValueError('运行目录内容改变')
            files[relative] = digest
            archive.write(path, relative)
    if set(files) != set(manifest['files']) | {'BUNDLE_MANIFEST.json'}:
        raise ValueError('运行目录文件集合不完整')
    info = dict(package_id=PACKAGE_ID, commit=COMMIT, source_tree=TREE,
        zip_sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(), files=files,
        signed=False, ordinary_acceptance=False, no_python_clean_host_acceptance=False,
        disconnected_host_acceptance=False, realtime_accepted=False)
    (payload / 'PAYLOAD.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    command = [sys.executable, '-I', '-B', '-m', 'PyInstaller', '--noconfirm', '--onefile',
        '--windowed', '--noupx', '--name', 'Hakimi_Blackjack_T1O1_Offline_Setup',
        '--paths', str(HERE.parent / 'windows_trial'), '--add-data', str(payload) + ':payload',
        '--workpath', str(output / 'pyi-work'), '--distpath', str(output / 'dist'),
        '--specpath', str(output), str(HERE / 'offline_setup.py')]
    with (output / 'setup-build.log').open('xb') as log:
        run = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    if run.returncode:
        raise RuntimeError('安装器冻结失败；保留日志')
    if wrapper_identity() != wrapper_before:
        raise RuntimeError('安装器构建期间包装源码改变；保留本轮，不签署产物')
    executable = output / 'dist/Hakimi_Blackjack_T1O1_Offline_Setup.exe'
    receipt = dict(package_id=PACKAGE_ID, application_commit=COMMIT, source_tree=TREE,
        wrapper=wrapper_before, runtime_wrapper_commit=manifest.get('wrapper_commit'),
        runtime_manifest_sha256=hashlib.sha256((runtime / 'BUNDLE_MANIFEST.json').read_bytes()).hexdigest(),
        setup_path=str(executable), setup_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        setup_bytes=executable.stat().st_size, payload_zip_sha256=info['zip_sha256'],
        signed=False, build_passed=True, actual_install_test=False,
        ordinary_permissions_test=False, clean_no_python_host_test=False,
        network_disabled_host_test=False, realtime_accepted=False)
    (output / 'SETUP_BUILD_RECEIPT.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
