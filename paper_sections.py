"""Bounded end-matter extraction; never send complete papers to the model."""
import re
import codecs
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import deque

NUMBER = r'(?:(?:\d+(?:\.\d+)*[.、]?|[IVX]+[.])\s*)?'
LABELS = {
    'conclusion': r'(?:discussion\s+and\s+conclusions?|conclusions?(?:\s+and\s+(?:future\s+work|outlook))?|concluding\s+remarks|结\s*论(?:与展望)?|总\s*结(?:与展望)?|结\s*语)',
    'limitations': r'(?:limitations?(?:\s+and\s+(?:future\s+work|future\s+directions))?|局限性?(?:与展望)?|研究局限(?:性)?|不足(?:与展望)?)',
}
STOP = r'(?:references|bibliography|acknowledg(?:e)?ments?|appendi(?:x|ces)|supplementary|funding|declarations?|author\s+contributions?|conflicts?\s+of\s+interest|参考文献|致谢|附录|作者贡献|利益冲突)'


def end_text(path):
    path = Path(path)
    if path.suffix.lower() == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        count = len(reader.pages)
        first = max(0, count - 12)
        text = '\n\n'.join((reader.pages[i].extract_text() or '') for i in range(first, count))
        return text, f'PDF 第 {first + 1}–{count} 页（最多末尾 12 页）'
    if path.suffix.lower() == '.docx':
        tail, length = deque(), 0
        with zipfile.ZipFile(path) as archive, archive.open('word/document.xml') as doc:
            for _, element in ET.iterparse(doc, events=('end',)):
                if element.tag.endswith('}p'):
                    value = ''.join(t.text or '' for t in element.iter() if t.tag.endswith('}t'))
                    tail.append(value)
                    length += len(value)
                    while length > 60000 and len(tail) > 1:
                        length -= len(tail.popleft())
                    element.clear()
        return '\n\n'.join(tail)[-60000:], 'DOCX 末尾文本（最多 60000 字符）'
    with path.open('rb') as source:
        sample = source.read(4096)
        try:
            codecs.getincrementaldecoder('utf-8-sig')().decode(sample, final=False)
            encoding = 'utf-8-sig'
        except UnicodeDecodeError:
            encoding = 'gb18030'
        source.seek(0, 2)
        source.seek(max(0, source.tell() - 96000))
        raw = source.read(96000)
    text = raw.decode(encoding, errors='ignore')
    return text, '文本末尾（最多 96000 字节）'


def parse_end_sections(text):
    text = text.replace('\r', '').replace('\x00', '')
    # Heading is constrained to its own line or an explicit colon. This avoids
    # treating prose such as "Conclusions drawn from..." as a section heading.
    found = []
    for kind, label in {**LABELS, 'stop': STOP}.items():
        pattern = r'(?im)^[ \t]*(?:#{1,5}[ \t]*)?' + NUMBER + label + r'[ \t]*(?:[:：][ \t]*|\.?[ \t]*(?:\n|$))'
        for match in re.finditer(pattern, text):
            found.append((match.start(), match.end(), kind))
    found.sort()
    result = {'conclusion': '', 'limitations': ''}
    for i, (start, end, kind) in enumerate(found):
        if kind == 'stop':
            continue
        stop = found[i + 1][0] if i + 1 < len(found) else len(text)
        body = text[end:stop].strip()
        # Also stop at an unrelated numbered section, before appendices etc.
        next_section = re.search(r'(?m)^\s*(?:\d+[.、]?|[IVX]+\.)\s+[A-Za-z\u4e00-\u9fff][^\n]{0,90}\n', body)
        if next_section:
            body = body[:next_section.start()].strip()
        if body:
            result[kind] = body[:6000 if kind == 'conclusion' else 3000]
    return result


def extract_end(path):
    if Path(path).suffix.lower() == '.pdf':
        return extract_pdf_parts(path)[2]
    text, source = end_text(path)
    result = parse_end_sections(text)
    recognized = '、'.join(label for key, label in [('conclusion', '结论'), ('limitations', '局限')] if result[key])
    return {**result, 'end_checked': 1, 'end_note': source + ('；已提取' + recognized if recognized else '；未识别结论／局限标题，可手动补充')}


def extract_pdf_parts(path):
    """Share a reader/page cache and search the middle only for missing sections."""
    from pypdf import PdfReader
    reader = PdfReader(path)
    count = len(reader.pages)
    cache = {}
    def page(index):
        if index not in cache:
            cache[index] = reader.pages[index].extract_text() or ''
        return cache[index]
    front_end = min(4, count)
    front = '\n\n'.join(page(i) for i in range(front_end))
    meta = reader.metadata
    title = str(meta.title or '') if meta else ''
    if title.lower().endswith(('.doc', '.docx', '.pdf')):
        title = ''
    result = {'conclusion': '', 'limitations': ''}
    try:
        tail_start = max(0, count - 12)
        tail = '\n\n'.join(page(i) for i in range(tail_start, count))
        # Join only contiguous pages. A gap could otherwise attach unrelated
        # appendix text to a conclusion started in the front matter.
        if tail_start <= front_end:
            result = parse_end_sections('\n\n'.join(page(i) for i in range(count)))
        else:
            result = parse_end_sections(front)
            tail_result = parse_end_sections(tail)
            result.update({k: v for k, v in tail_result.items() if v})
            if not all(result.values()):
                # Conclusions can precede a long appendix and lie outside the
                # last twelve pages. Reuse cached pages when filling that gap.
                complete = '\n\n'.join(page(i) for i in range(count))
                result = parse_end_sections(complete)
        missing = '、'.join(label for key, label in [('conclusion', '结论'), ('limitations', '局限')] if not result[key])
        note = f'PDF 已检查 {len(cache)}/{count} 页；页面解析结果复用'
        if missing:
            note += '；未识别' + missing + '标题，可手动补充'
        return front, title, {**result, 'end_checked': 1, 'end_note': note}
    except Exception:
        return front, title, {**result, 'end_checked': 1, 'end_note': '结论／局限读取不完整，可重新读取或手动补充。'}
