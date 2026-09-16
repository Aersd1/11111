"""Collision-safe title filenames with a database/file rollback on failure."""
import os
import re
import shutil
import threading
from pathlib import Path

_lock = threading.Lock()


def credible_title(title):
    if not isinstance(title, str):
        return ''
    title = ' '.join(title.split()).strip(' #')
    if len(title) < 4 or title.lower() in ('abstract', 'introduction', 'untitled', 'preprint', 'paper', 'document', 'unknown', 'not available', '摘要', '无标题', '未知标题'):
        return ''
    if re.match(r'(?i)^(published as|under review|arxiv:|accepted (at|by)|proceedings of)', title):
        return ''
    return title[:400]


def title_filename(title):
    title = credible_title(title)
    if not title:
        return ''
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip(' .')[:140].rstrip(' .')
    if re.fullmatch(r'(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name):
        name = '_' + name
    return name


def rename_imported(library, ident, title):
    name = title_filename(title)
    if not name:
        return None
    with _lock:
        paper = library.get(ident)
        source = Path(paper['path'])
        target = source.with_name(name + source.suffix)
        if os.path.normcase(str(source.resolve())) == os.path.normcase(str(target.resolve())):
            return source
        number = 2
        while True:
            try:
                stream = target.open('xb')
                break
            except FileExistsError:
                target = source.with_name(f'{name} ({number}){source.suffix}')
                number += 1
        changed_db = False
        try:
            with stream, source.open('rb') as original:
                shutil.copyfileobj(original, stream)
            shutil.copystat(source, target)
            library.update(ident, path=str(target))
            changed_db = True
            source.unlink()
        except Exception:
            if changed_db:
                library.update(ident, path=str(source))
            target.unlink(missing_ok=True)
            raise
        return target
