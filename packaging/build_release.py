"""Build on the target OS; explicit assets only, never bundle private user data."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / '.build-tools'), str(ROOT / '.vendor')]
OUT = ROOT / 'release'
NAME = 'LiteratureShelf'
DIST = ROOT / os.environ.get('LITERATURE_BUILD_DIST', 'dist')


def main():
    os.chdir(ROOT)
    os.environ['PYINSTALLER_CONFIG_DIR'] = str(ROOT / 'build' / 'pyinstaller-cache')
    if sys.platform == 'win32':
        # Prevent unrelated PDF/image tooling on PATH from injecting incompatible ICU/UCRT DLLs.
        windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        os.environ['PATH'] = os.pathsep.join(map(str, [Path(sys.base_prefix), Path(sys.base_prefix) / 'DLLs', windows / 'System32', windows]))
    OUT.mkdir(exist_ok=True)
    licenses = ROOT / 'build' / 'licenses'
    licenses.mkdir(parents=True, exist_ok=True)
    for package in ('PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'pypdf'):
        dist = importlib.metadata.distribution(package)
        for file in dist.files or []:
            if 'license' in str(file).lower() or 'copying' in str(file).lower():
                source = Path(dist.locate_file(file))
                if source.is_file():
                    dest = licenses / package / str(file).replace('..', '_')
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
    import PyInstaller.__main__
    args = ['desktop_entry.py', '--name', NAME, '--noconfirm', '--clean', '--windowed', '--onedir',
            '--distpath', str(DIST),
            '--paths', str(ROOT), '--add-data', f'{ROOT / "packaging" / "QUICK_START.md"}{os.pathsep}.',
            '--add-data', f'{licenses}{os.pathsep}licenses', '--hidden-import', 'pypdf',
            '--add-data', f'{ROOT / "assets" / "katex"}{os.pathsep}assets/katex',
            '--hidden-import', 'agent_search', '--exclude-module', 'tkinter',
            '--exclude-module', 'numpy', '--exclude-module', 'PIL', '--exclude-module', 'psutil', '--exclude-module', 'fontTools']
    if (ROOT / '.vendor').is_dir():
        args += ['--paths', str(ROOT / '.vendor')]
    if sys.platform == 'darwin':
        args += ['--osx-bundle-identifier', 'io.literatureshelf.desktop']
    if sys.platform.startswith('linux'):
        # Qt's XCB plugin loads these desktop libraries at runtime; bundle the optional ones.
        output = subprocess.check_output(['ldconfig', '-p'], text=True)
        names = ('libxcb-cursor.so', 'libxcb-icccm.so', 'libxcb-keysyms.so', 'libxcb-image.so',
                 'libxcb-render-util.so', 'libxcb-xinerama.so', 'libxcb-xkb.so', 'libxkbcommon-x11.so')
        for line in output.splitlines():
            if any(line.strip().startswith(n) for n in names) and '=>' in line:
                args += ['--add-binary', line.split('=>')[-1].strip() + os.pathsep + '.']
    PyInstaller.__main__.run(args)
    folder = DIST / NAME
    executable = folder / (NAME + '.exe' if sys.platform == 'win32' else NAME)
    if sys.platform == 'darwin':
        executable = DIST / (NAME + '.app') / 'Contents' / 'MacOS' / NAME
    smoke_dir = ROOT / 'build' / 'packaged-smoke'
    subprocess.run([str(executable), '--smoke-test', str(smoke_dir)], check=True, timeout=120)
    if not json.loads((smoke_dir / 'smoke-result.json').read_text('utf-8')).get('ok'):
        raise RuntimeError('Packaged smoke test failed')
    shutil.copy2(ROOT / 'packaging' / 'QUICK_START.md', folder / '使用说明.md')
    arch = platform.machine().lower().replace('amd64', 'x86_64')
    if sys.platform == 'darwin':
        stage = ROOT / 'build' / 'dmg'
        stage.mkdir(exist_ok=True)
        target = stage / (NAME + '.app')
        shutil.copytree(DIST / (NAME + '.app'), target, dirs_exist_ok=True, symlinks=True)
        shutil.copy2(ROOT / 'packaging' / 'QUICK_START.md', stage / '使用说明.md')
        if not (stage / 'Applications').is_symlink():
            (stage / 'Applications').symlink_to('/Applications')
        artifact = OUT / f'{NAME}-macOS-{arch}.dmg'
        subprocess.run(['hdiutil', 'create', '-volname', NAME, '-srcfolder', str(stage), '-ov', '-format', 'UDZO', str(artifact)], check=True)
    elif sys.platform == 'win32':
        artifact = OUT / f'{NAME}-Windows-{arch}.zip'
        with zipfile.ZipFile(artifact, 'w', zipfile.ZIP_DEFLATED) as archive:
            for file in folder.rglob('*'):
                if file.is_file():
                    archive.write(file, file.relative_to(folder.parent))
    else:
        launcher = folder / '启动文献书架.sh'
        launcher.write_text('#!/bin/sh\ncd -- "$(dirname -- "$0")" || exit 1\nexec ./LiteratureShelf "$@"\n', 'utf-8')
        launcher.chmod(0o755)
        artifact = OUT / f'{NAME}-Linux-{arch}.tar.gz'
        with tarfile.open(artifact, 'w:gz') as archive:
            archive.add(folder, arcname=NAME)
    digest = hashlib.file_digest(artifact.open('rb'), 'sha256').hexdigest()
    artifact.with_suffix(artifact.suffix + '.sha256').write_text(digest + '  ' + artifact.name + '\n', 'utf-8')
    print('RELEASE:', artifact, flush=True)


if __name__ == '__main__':
    main()
