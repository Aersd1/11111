"""Validated, atomic API configuration editing without discarding extra fields."""
from __future__ import annotations
import copy
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit


def validate_config(document):
    if not isinstance(document, dict) or not isinstance(document.get('OpenAI'), dict):
        raise ValueError('配置需要包含 OpenAI 对象。')
    cfg = document['OpenAI']
    for key, label in [('base_url', '接口地址'), ('api_key', 'API Key'), ('model', '模型名称')]:
        if not isinstance(cfg.get(key), str) or not cfg[key].strip():
            raise ValueError(f'请填写{label}。')
    url = urlsplit(cfg['base_url'].strip())
    if not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('接口地址格式不正确，请填写不含查询参数的 Base URL。')
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('远程接口需要 HTTPS；本地接口可使用 HTTP。')
    maximum = cfg.get('max_tokens', 2500)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 1000000:
        raise ValueError('max_tokens 必须是 1–1000000 之间的整数。')
    temperature = cfg.get('temperature', 0.2)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
        raise ValueError('temperature 必须在 0–2 之间。')
    return copy.deepcopy(document)


def read_config(path):
    try:
        document = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise ValueError('无法读取有效 JSON，请检查文件位置和内容。') from None
    return validate_config(document)


def write_config(path, document):
    document = validate_config(document)
    target = Path(path).expanduser().resolve()
    if target.suffix.lower() != '.json':
        raise ValueError('请保存为 .json 配置文件。')
    if not target.parent.is_dir():
        raise ValueError('配置文件所在目录不存在。')
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent, suffix='.tmp', delete=False) as stream:
            staged = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            shutil.copy2(target, target.with_suffix(target.suffix + '.bak'))
        os.replace(staged, target)
    except OSError:
        raise ValueError('无法保存接口文件，请检查文件权限或另存到可写位置。') from None
    finally:
        if staged and staged.exists():
            staged.unlink()
    return target
