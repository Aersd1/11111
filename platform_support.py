"""Native file opening and writable paths, shared by source and packaged builds."""
import os
import sys
import subprocess
from pathlib import Path


def data_root():
    if os.environ.get('LITERATURE_DATA_DIR'):
        return Path(os.environ['LITERATURE_DATA_DIR']).expanduser().resolve()
    if not getattr(sys, 'frozen', False):
        return Path(__file__).resolve().parent
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'LiteratureShelf'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'LiteratureShelf'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'LiteratureShelf'


def open_native(path, edit=False):
    path = str(Path(path).resolve())
    if sys.platform == 'win32':
        if edit:
            subprocess.Popen(['notepad.exe', path])
        else:
            os.startfile(path)
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', '-t', path] if edit else ['open', path])
    else:
        subprocess.Popen(['xdg-open', path])


def valid_program(path):
    path = Path(path)
    if sys.platform == 'darwin' and path.is_dir() and path.suffix.lower() == '.app':
        return True
    return path.is_file() and (path.suffix.lower() == '.exe' if sys.platform == 'win32' else os.access(path, os.X_OK))


def open_with(program, source):
    if not valid_program(program):
        raise ValueError('请选择有效的阅读程序（Windows .exe、macOS .app 或可执行程序）。')
    if sys.platform == 'darwin' and Path(program).suffix.lower() == '.app':
        subprocess.Popen(['open', '-a', str(program), str(source)])
    else:
        subprocess.Popen([str(program), str(source)])
