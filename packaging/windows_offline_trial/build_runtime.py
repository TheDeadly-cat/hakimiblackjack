"""Construct a fresh onedir trial from exact Git blobs in an isolated build env."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tkinter
import urllib.request

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / 'windows_trial'))
from trial_common import COMMIT, TREE, safe_relative, tree_hash, verify_installed_source

PACKAGE_ID = 'T1O1-3ec2006'
NAME = 'HakimiBlackjackTrialT1O1'


def git(*args):
    return subprocess.check_output(['git', '-C', str(REPO), *args])


def dump_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def runtime_source_data(source, manifest):
    # Frozen module __file__ paths are below _internal/blackjack_lab. The
    # production snapshot stores hash these real source files there.
    return [(str(source / relative), str(Path(relative).parent).replace('\\', '/'))
        for relative in manifest['files'] if relative.startswith('blackjack_lab/')]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wheelhouse', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError('构建输出必须是新目录，保留原产物及失败现场。')
    if importlib.metadata.version('pyinstaller') != '6.22.3':
        raise ValueError('打包器版本不符')
    if sys.version_info[:3] != (3, 14, 6):
        raise ValueError('构建解释器版本不符')
    output.mkdir(parents=True)
    source = output / 'source'
    source.mkdir()
    manifest = dict(schema='hakimi-trial-source-manifest-v1', commit=COMMIT, git_tree=TREE, files={})
    tree_entries = {}
    for row in git('ls-tree', '-r', '-z', COMMIT).split(b'\0'):
        if not row:
            continue
        metadata, raw_name = row.split(b'\t', 1)
        mode, kind, blob = metadata.decode('ascii').split()
        relative = safe_relative(raw_name.decode('utf-8'))
        if kind != 'blob' or mode not in ('100644', '100755'):
            raise ValueError('不支持的源码类型')
        if Path(relative).suffix.lower() in ('.db', '.sqlite', '.sqlite3', '.ttf', '.otf', '.woff', '.woff2'):
            raise ValueError('不得在试用产物中加入资料数据库或额外字体')
        data = git('cat-file', 'blob', blob)
        target = source.joinpath(*relative.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(data)
        manifest['files'][relative] = dict(git_mode=mode, git_blob=blob,
            sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        tree_entries[relative] = (mode, blob)
    if len(tree_entries) != 1539 or tree_hash(tree_entries) != TREE:
        raise ValueError('完整固定源码树不符')
    verify_installed_source(source, manifest)
    dump_new(output / 'source-manifest.json', manifest)
    prepare = subprocess.run([sys.executable, '-I', '-B', '-c',
        'import sys;sys.path.insert(0,sys.argv[1]);from blackjack_lab.analysis.native_backend import build_native;print(build_native())',
        str(source)], capture_output=True, text=True, timeout=60)
    (output / 'native-preparation.log').write_text(prepare.stdout + prepare.stderr, encoding='utf-8')
    if prepare.returncode:
        raise RuntimeError('固定源码的数值程序准备失败')
    cache = source / 'blackjack_lab/.local-native/ab903877633dd08765c9f7b13bc8c56154ac0a766b7ecd6d5cff128c84b63fee'
    if not (cache / 'SplitEngine.exe').is_file() or not (cache / 'build.json').is_file():
        raise RuntimeError('数值程序或源绑定回执缺失')
    licenses = output / 'licenses'
    licenses.mkdir()
    base = Path(sys.base_prefix)
    shutil.copy2(base / 'LICENSE.txt', licenses / 'Python-LICENSE.txt')
    shutil.copy2(base / 'tcl/tk8.6/license.terms', licenses / 'Tk-license.terms')
    packer = importlib.metadata.distribution('pyinstaller')
    for item in packer.files:
        if str(item).startswith('pyinstaller-6.22.3.dist-info/licenses/'):
            shutil.copy2(packer.locate_file(item), licenses / ('PyInstaller-' + Path(item).name))
    tcl_url = 'https://raw.githubusercontent.com/tcltk/tcl/core-8-6-15/license.terms'
    with urllib.request.urlopen(tcl_url, timeout=30) as response:
        tcl_terms = response.read(128 * 1024)
    if not tcl_terms.startswith(b'This software is copyrighted'):
        raise ValueError('Tcl许可证材料不符')
    (licenses / 'Tcl-license.terms').write_bytes(tcl_terms)
    base_info = dict(python=sys.version, python_executable=sys.executable,
        pyinstaller=importlib.metadata.version('pyinstaller'),
        tcl_patch=tkinter.Tcl().eval('info patchlevel'),
        package_id=PACKAGE_ID, commit=COMMIT, source_tree=TREE,
        wheel_hashes={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(args.wheelhouse.glob('*.whl'))},
        build_packages={distribution.metadata['Name']: distribution.version
            for distribution in importlib.metadata.distributions()},
        wrapper_files={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(HERE.glob('*.py'))},
        wrapper_commit=git('rev-parse', 'HEAD').decode().strip(),
        wrapper_worktree_status=git('status', '--porcelain').decode(),
        Tcl_license_source=tcl_url, public_release=False,
        signed=False, clean_host_acceptance=False, realtime_accepted=False)
    dump_new(output / 'build-environment.json', base_info)
    datas = [(str(source / relative), 'app/' + str(Path(relative).parent).replace('\\', '/'))
        for relative in manifest['files']]
    datas += runtime_source_data(source, manifest)
    datas += [(str(output / 'source-manifest.json'), '.'), (str(licenses), 'licenses'),
        (str(cache / 'SplitEngine.exe'), 'blackjack_lab/.local-native/' + cache.name),
        (str(cache / 'build.json'), 'blackjack_lab/.local-native/' + cache.name)]
    spec = output / 'offline_trial.spec'
    spec.write_text(f'''import sys,hashlib,json\nfrom pathlib import Path\nfrom PyInstaller.utils.hooks import collect_submodules\nsys.path.insert(0,{str(source)!r})\na=Analysis([{str(HERE / 'offline_launcher.py')!r}],pathex=[{str(HERE)!r},{str(HERE.parent / 'windows_trial')!r},{str(source)!r}],datas={datas!r},hiddenimports=collect_submodules('blackjack_lab'),excludes=['pytest','PIL','cv2','numpy','matplotlib'])\nexpected=json.loads(Path({str(output / 'source-manifest.json')!r}).read_text(encoding='utf-8'))['files']\norigins={{}}\nfor name,path,kind in a.pure:\n    if name=='blackjack_lab' or name.startswith('blackjack_lab.'):\n        p=Path(path).resolve()\n        rel=p.relative_to(Path({str(source)!r}).resolve()).as_posix()\n        digest=hashlib.sha256(p.read_bytes()).hexdigest()\n        assert digest==expected[rel]['sha256'],(name,rel)\n        origins[name]={{'source_path':rel,'sha256':digest}}\nassert origins\nPath({str(output / 'compiled-module-source-origins.json')!r}).write_text(json.dumps(origins,indent=2),encoding='utf-8')\npyz=PYZ(a.pure)\nexe=EXE(pyz,a.scripts,[],exclude_binaries=True,name={NAME!r},debug=False,strip=False,upx=False,console=False)\ncoll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name={NAME!r})\n''', encoding='utf-8')
    command = [sys.executable, '-I', '-B', '-m', 'PyInstaller', '--noconfirm',
        '--workpath', str(output / 'pyi-work'), '--distpath', str(output / 'dist'), str(spec)]
    with (output / 'pyinstaller-build.log').open('xb') as log:
        build = subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    if build.returncode:
        raise RuntimeError('冻结构建失败；保留完整日志。')
    current_wrapper = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(HERE.glob('*.py'))}
    if current_wrapper != base_info['wrapper_files']:
        raise RuntimeError('构建期间包装源码改变；保留本轮，不签署产物。')
    runtime = output / 'dist' / NAME
    shutil.copy2(output / 'compiled-module-source-origins.json',
        runtime / '_internal/compiled-module-source-origins.json')
    batches = {
        'START_TRIAL.cmd': '@echo off\r\nstart /wait "" "%~dp0' + NAME + '.exe"\r\nexit /b %errorlevel%\r\n',
        'BACKUP_DATA.cmd': '@echo off\r\nstart /wait "" "%~dp0' + NAME + '.exe" --maintenance backup %*\r\nexit /b %errorlevel%\r\n',
        'VERIFY_SOURCE.cmd': '@echo off\r\nstart /wait "" "%~dp0' + NAME + '.exe" --maintenance verify %*\r\nexit /b %errorlevel%\r\n',
        'UNINSTALL_TRIAL.cmd': '@echo off\r\nstart /wait "" "%~dp0' + NAME + '.exe" --maintenance prepare-uninstall\r\nif errorlevel 1 exit /b 1\r\n"%~dp0..\\..\\HakimiBJTrialInstallLogs\\' + PACKAGE_ID + '-uninstall.cmd" %*\r\n',
    }
    for name, body in batches.items():
        (runtime / name).write_bytes(body.encode('utf-8'))
    files = {path.relative_to(runtime).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(runtime.rglob('*')) if path.is_file()}
    if not (runtime / '_internal/python314.dll').is_file():
        raise RuntimeError('冻结运行库缺少Python DLL')
    dump_new(runtime / 'BUNDLE_MANIFEST.json', dict(schema='hakimi-frozen-bundle-v1',
        package_id=PACKAGE_ID, commit=COMMIT, source_tree=TREE, files=files,
        wrapper_commit=base_info['wrapper_commit'], wrapper_files=base_info['wrapper_files'],
        runtime_is_frozen=True, signed=False, local_preflight_passed=False,
        clean_no_python_host_test=False, network_disabled_host_test=False,
        ordinary_permissions_test=False, realtime_accepted=False))
    verify_installed_source(source, manifest)
    print(json.dumps(dict(runtime=str(runtime), files=len(files), package_id=PACKAGE_ID,
        build_passed=True, application_preflight_passed=False, installer_built=False,
        realtime_accepted=False), ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
