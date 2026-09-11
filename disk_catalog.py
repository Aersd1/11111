"""Materialize the index as folders and Windows Internet shortcuts to originals."""
import json
import re
import os
import sys
from pathlib import Path


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip().rstrip('.')[:100] or '未命名'
    if value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        value = '_' + value
    return value


def sync_catalog(library):
    root = library.catalog_root
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / '.catalog-index.json'
    previous = json.loads(manifest.read_text('utf-8')) if manifest.exists() else {'files': {}, 'dirs': []}
    wanted, dirs = {}, set()
    for major, minor in library.categories():
        parent = safe_name(major)
        dirs.add(parent)
        if minor:
            dirs.add(parent + '/' + safe_name(minor))
    for paper in library.all():
        folder = safe_name(paper['major']) + '/' + safe_name(paper['minor'])
        dirs.update((safe_name(paper['major']), folder))
        name = folder + '/' + safe_name(paper['title']) + f" [{paper['id']}]"
        if sys.platform == 'win32':
            wanted[name + '.url'] = '[InternetShortcut]\nURL=' + Path(paper['path']).resolve().as_uri() + '\n'
        else:
            wanted[name + Path(paper['path']).suffix] = '@symlink:' + str(Path(paper['path']).resolve())
    def inside(relative):
        path = root / relative
        if not path.parent.resolve().is_relative_to(root.resolve()):
            raise ValueError('目录路径超出文献目录，已停止同步。')
        return path
    for directory in sorted(dirs):
        directory_path = inside(directory)
        if directory_path.is_symlink():
            raise ValueError('分类文件夹不能是指向外部位置的链接。')
        directory_path.mkdir(parents=True, exist_ok=True)
    def read_entry(path):
        return '@symlink:' + os.readlink(path) if path.is_symlink() else path.read_text('utf-8-sig')
    for relative, content in wanted.items():
        path = inside(relative)
        if path.exists() or path.is_symlink():
            old = read_entry(path)
            if old == content:
                continue
            if old != previous['files'].get(relative):
                raise ValueError('同名快捷方式已被外部修改，请先重命名该快捷方式：' + str(path))
        if path.is_symlink():
            path.unlink()
        if content.startswith('@symlink:'):
            if path.exists():
                path.unlink()
            path.symlink_to(content[len('@symlink:'):])
        else:
            path.write_text(content, encoding='utf-8-sig')
    for relative, content in previous['files'].items():
        path = inside(relative)
        if relative not in wanted and (path.is_file() or path.is_symlink()) and read_entry(path) == content:
            path.unlink()
    for directory in sorted(set(previous['dirs']) - dirs, key=len, reverse=True):
        path = inside(directory)
        if path.is_dir():
            try:
                path.rmdir()  # Only empty directories; never remove users' files.
            except OSError:
                pass
    staged = manifest.with_suffix('.tmp')
    staged.write_text(json.dumps({'files': wanted, 'dirs': sorted(dirs)}, ensure_ascii=False), 'utf-8')
    staged.replace(manifest)
    return root
