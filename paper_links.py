import json
import os
from pathlib import Path
import webbrowser
from urllib.parse import urlsplit
from urllib.request import url2pathname


def validate_target(target):
    target = target.strip()
    if Path(target).is_absolute():
        return target
    parsed = urlsplit(target)
    if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password:
        return target
    if parsed.scheme == 'file':
        return target
    raise ValueError('链接请填写 HTTP(S) 网页地址、file:// 链接或本地文件绝对路径。')


def parse_links(text):
    links = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split('|', 1)
        label, target = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ('', parts[0].strip())
        target = validate_target(target)
        links.append({'label': label or target, 'url': target})
    if len(links) > 30:
        raise ValueError('每篇文献最多保存 30 个关联链接。')
    return json.dumps(links, ensure_ascii=False)


def decode_links(value):
    try:
        parsed = json.loads(value or '[]')
        return [r for r in parsed if isinstance(r, dict) and isinstance(r.get('url'), str) and isinstance(r.get('label'), str)]
    except (ValueError, TypeError):
        return []


def links_text(value):
    return '\n'.join(r['label'] + ' | ' + r['url'] for r in decode_links(value))


def open_link(target):
    target = validate_target(target)
    parsed = urlsplit(target)
    if parsed.scheme in ('http', 'https'):
        webbrowser.open(target)
        return
    if parsed.scheme == 'file':
        target = url2pathname(parsed.path)
        if parsed.netloc and parsed.netloc != 'localhost':
            target = '\\\\' + parsed.netloc + target
    if not Path(target).is_file():
        raise ValueError('关联文件不存在，请检查路径或磁盘连接。')
    from platform_support import open_native
    open_native(target)
