"""Vendor pinned KaTeX assets for fully offline formula preview."""
import io
from pathlib import Path
import tarfile
import urllib.request

root = Path(__file__).resolve().parents[1] / 'assets/katex'
root.mkdir(parents=True, exist_ok=True)
url = 'https://registry.npmjs.org/katex/-/katex-0.18.7.tgz'
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=40) as response:
    data = response.read()
with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
    for member in archive.getmembers():
        name = member.name
        if not member.isfile():
            continue
        if name in ('package/dist/katex.min.js', 'package/dist/katex.min.css', 'package/dist/contrib/auto-render.min.js', 'package/LICENSE') or name.startswith('package/dist/fonts/'):
            relative = name.removeprefix('package/dist/').removeprefix('package/')
            target = root / relative
            if not target.resolve().is_relative_to(root.resolve()):
                raise ValueError('Invalid archive path')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
print('Offline KaTeX assets installed.')
