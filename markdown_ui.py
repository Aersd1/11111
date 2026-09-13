"""Markdown authoring in Qt, offline KaTeX formula typesetting in the system browser."""
import html
import json
from pathlib import Path
import re
import uuid
import webbrowser
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument, QShortcut, QKeySequence
from PySide6.QtWidgets import QPlainTextEdit, QTextBrowser, QHBoxLayout, QWidget
from platform_support import data_root
from ui_theme import button


def markdown_html(text):
    formulas = []
    def protect(match):
        formulas.append(match.group())
        return f'MATHPLACEHOLDERX{len(formulas) - 1}X'
    protected = re.sub(r'\$\$[\s\S]*?\$\$|(?<!\\)\$[^\n$]+?(?<!\\)\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)', protect, text)
    doc = QTextDocument()
    doc.setMarkdown(protected, QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML)
    rendered = doc.toHtml()
    rendered = re.sub(r'<img\b[^>]*>', '', rendered, flags=re.I)
    rendered = re.sub(r'href="([^"]*)"', lambda m: m.group() if html.unescape(m[1]).lower().startswith(('https://', 'http://', 'file://', '#')) else '', rendered)
    for n, formula in enumerate(formulas):
        rendered = rendered.replace(f'MATHPLACEHOLDERX{n}X', html.escape(formula))
    return rendered


def open_typeset(text):
    assets = Path(__file__).resolve().parent / 'assets/katex'
    if not (assets / 'katex.min.js').is_file():
        raise ValueError('离线公式组件未找到，请使用完整安装包。')
    body = re.search(r'<body[^>]*>([\s\S]*)</body>', markdown_html(text)).group(1)
    options = {'throwOnError': False, 'trust': False, 'maxExpand': 1000,
        'delimiters': [{'left': '$$', 'right': '$$', 'display': True}, {'left': '$', 'right': '$', 'display': False},
                       {'left': '\\[', 'right': '\\]', 'display': True}, {'left': '\\(', 'right': '\\)', 'display': False}]}
    document = '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src file: \'unsafe-inline\'; style-src file: \'unsafe-inline\'; font-src file: data:; img-src data:">'
    document += '<title>文献排版预览</title><link rel="stylesheet" href="' + (assets / 'katex.min.css').as_uri() + '">'
    document += '<style>body{max-width:960px;margin:40px auto;padding:0 28px;font:17px/1.8 sans-serif;color:#292a43}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:8px}pre{white-space:pre-wrap}.katex-display{overflow:auto}</style></head><body>' + body
    document += '<script src="' + (assets / 'katex.min.js').as_uri() + '"></script><script src="' + (assets / 'contrib/auto-render.min.js').as_uri() + '"></script>'
    document += '<script>renderMathInElement(document.body,' + json.dumps(options) + ');</script></body></html>'
    folder = data_root() / 'previews'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (uuid.uuid4().hex + '.html')
    path.write_text(document, 'utf-8')
    webbrowser.open(path.as_uri())
    return path


class MarkdownEdit(QPlainTextEdit):
    def __init__(self, text=''):
        super().__init__(text)
        self.setPlaceholderText('支持 Markdown：**加粗**、*斜体*、# 标题；LaTeX：$E=mc^2$ 或 $$\\frac{a}{b}$$')
        QShortcut(QKeySequence('Ctrl+B'), self, activated=lambda: self.wrap('**'))
        QShortcut(QKeySequence('Ctrl+I'), self, activated=lambda: self.wrap('*'))

    def wrap(self, marker):
        cursor = self.textCursor()
        value = cursor.selectedText().replace('\u2029', '\n') or ('公式' if '$' in marker else '文字')
        start = cursor.selectionStart()
        cursor.insertText(marker + value + marker)
        cursor.setPosition(start + len(marker))
        cursor.setPosition(start + len(marker) + len(value), cursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        self.setFocus()


def editor_toolbar(current_editor, preview_callback=None):
    bar = QWidget()
    row = QHBoxLayout(bar)
    row.setContentsMargins(0, 0, 0, 0)
    for text, marker in [('B 加粗', '**'), ('I 斜体', '*'), ('行内公式', '$'), ('独立公式', '$$')]:
        row.addWidget(button(text, lambda m=marker: current_editor().wrap(m) if isinstance(current_editor(), MarkdownEdit) else None, 'ghost'))
    if preview_callback:
        row.addWidget(button('排版预览', preview_callback, 'soft'))
    row.addStretch()
    return bar


def markdown_view(text, minimum=160):
    view = QTextBrowser()
    view.setOpenLinks(False)
    view.anchorClicked.connect(lambda url: webbrowser.open(url.toString()) if url.scheme() in ('http', 'https') else None)
    view.setHtml(markdown_html(text))
    view.setMinimumHeight(minimum)
    view.setStyleSheet('QTextBrowser { background:white; border:none; padding:0; }')
    return view
