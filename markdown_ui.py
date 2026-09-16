"""Markdown authoring and live, offline KaTeX rendering inside the desktop app."""
import html
import json
from pathlib import Path
import re
import uuid
import webbrowser
import atexit
import os
# Text and math previews need no GPU; software composition also works on CI and VMs.
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
from PySide6.QtCore import Qt, QUrl, QTimer, QCoreApplication, QEvent
from PySide6.QtGui import QTextDocument, QShortcut, QKeySequence
from PySide6.QtWidgets import (QPlainTextEdit, QTextBrowser, QHBoxLayout, QWidget, QDialog, QVBoxLayout,
    QFormLayout, QLineEdit, QFileDialog, QMessageBox, QApplication, QLabel)
QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from platform_support import data_root
from ui_theme import button
from paper_links import validate_target, open_link
from urllib.parse import quote, unquote, urlsplit


def _shutdown_previews():
    # Dispose browser pages before QApplication/default-profile teardown.
    from shiboken6 import delete, isValid
    app = QApplication.instance()
    if app is not None and isValid(app):
        for widget in list(app.allWidgets()):
            if isinstance(widget, QWebEngineView) and isValid(widget):
                delete(widget)


atexit.register(_shutdown_previews)


MATH_PATTERN = r'```(?:math|latex)\s*\n[\s\S]*?```|```[\s\S]*?```|`[^`\n]+`|(?<!\\)\$\$[\s\S]*?(?<!\\)\$\$|(?<![\\$])\$[^\n$]+?(?<!\\)\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\\begin\{(?:align\*?|equation\*?|gather\*?)\}[\s\S]*?\\end\{(?:align\*?|equation\*?|gather\*?)\}'


def markdown_html(text, base_dir=None):
    formulas = []
    def protect(match):
        value = match.group()
        if value.startswith('`') and not re.match(r'```(?:math|latex)\s*\n', value):
            return value
        formulas.append(value)
        return f'MATHPLACEHOLDERX{len(formulas) - 1}X'
    protected = re.sub(MATH_PATTERN, protect, text)
    doc = QTextDocument()
    doc.setMarkdown(protected, QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML)
    rendered = doc.toHtml()
    def local_image(match):
        src = re.search(r'src="([^"]*)"', match.group())
        if not src or base_dir is None:
            return ''
        target = html.unescape(src[1])
        parsed = urlsplit(target)
        if parsed.scheme not in ('', 'file'):
            return '<span>[远程图片未加载]</span>'
        root = Path(base_dir).resolve()
        raw = unquote(parsed.path)
        if parsed.scheme == 'file' and re.match(r'^/[A-Za-z]:', raw):
            raw = raw[1:]
        path = (root / raw).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'):
            return '<span>[图片未找到或格式不支持]</span>'
        return '<img src="' + html.escape(path.as_uri(), quote=True) + '" loading="lazy">'
    rendered = re.sub(r'<img\b[^>]*>', local_image, rendered, flags=re.I)
    rendered = re.sub(r'href="([^"]*)"', lambda m: m.group() if html.unescape(m[1]).lower().startswith(('https://', 'http://', 'file://', '#')) else '', rendered)
    for n, formula in enumerate(formulas):
        display = formula.startswith(('$$', '\\[', '\\begin', '```'))
        if formula.startswith('```'):
            tex = formula.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        elif formula.startswith(('$$', '\\[', '\\(')):
            tex = formula[2:-2]
        elif formula.startswith('$'):
            tex = formula[1:-1]
        else:
            tex = formula
        tag = '<span class="math-source" data-display="' + str(display).lower() + '" data-tex="' + html.escape(tex, quote=True) + '">' + html.escape(formula) + '</span>'
        rendered = rendered.replace(f'MATHPLACEHOLDERX{n}X', tag)
    return rendered


MATH_SCRIPT = "document.querySelectorAll('.math-source').forEach(n=>katex.render(n.dataset.tex,n,{displayMode:n.dataset.display==='true',throwOnError:false,trust:false,strict:'ignore',maxExpand:1000}));"


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
    document += '<script>const mathOptions=' + json.dumps(options) + ';' + MATH_SCRIPT + '</script></body></html>'
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
        QShortcut(QKeySequence('Ctrl+K'), self, activated=self.link_dialog)

    def wrap(self, marker):
        cursor = self.textCursor()
        value = cursor.selectedText().replace('\u2029', '\n') or ('公式' if '$' in marker else '文字')
        start = cursor.selectionStart()
        cursor.insertText(marker + value + marker)
        cursor.setPosition(start + len(marker))
        cursor.setPosition(start + len(marker) + len(value.encode('utf-16-le')) // 2, cursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        self.setFocus()

    def insert_link(self, caption, target):
        target = validate_target(target)
        if Path(target).is_absolute():
            target = Path(target).as_uri()
        target = quote(target, safe=':/?#@!$&\'*,;=+%~')
        caption = (caption.strip() or target).replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]').replace('\n', ' ')
        self.textCursor().insertText(f'[{caption}](<{target}>)')
        self.setFocus()

    def link_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('插入链接')
        dialog.resize(540, 220)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        caption = QLineEdit(self.textCursor().selectedText())
        target = QLineEdit()
        target.setPlaceholderText('https://网页地址 或本地文件路径')
        form.addRow('显示文字', caption)
        form.addRow('链接地址', target)
        layout.addLayout(form)
        def browse():
            path, _ = QFileDialog.getOpenFileName(dialog, '选择关联文件')
            if path:
                target.setText(path)
                if not caption.text(): caption.setText(Path(path).name)
        layout.addWidget(button('选择本地文件…', browse, 'soft'))
        def save():
            try:
                self.insert_link(caption.text(), target.text())
            except ValueError as exc:
                QMessageBox.warning(dialog, '链接格式错误', str(exc))
                return
            dialog.accept()
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button('取消', dialog.reject))
        row.addWidget(button('插入', save, 'primary'))
        layout.addLayout(row)
        dialog.exec()


def editor_toolbar(current_editor, preview_callback=None):
    bar = QWidget()
    row = QHBoxLayout(bar)
    row.setContentsMargins(0, 0, 0, 0)
    for text, marker in [('B 加粗', '**'), ('I 斜体', '*'), ('行内公式', '$'), ('独立公式', '$$')]:
        row.addWidget(button(text, lambda m=marker: current_editor().wrap(m) if isinstance(current_editor(), MarkdownEdit) else None, 'ghost'))
    row.addWidget(button('插入链接', lambda: current_editor().link_dialog() if isinstance(current_editor(), MarkdownEdit) else None, 'soft'))
    if preview_callback:
        row.addWidget(button('排版预览', preview_callback, 'soft'))
    row.addStretch()
    return bar


class PreviewPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, kind, main_frame):
        if kind == self.NavigationType.NavigationTypeLinkClicked:
            if url.hasFragment() and url.adjusted(QUrl.UrlFormattingOption.RemoveFragment) == self.url().adjusted(QUrl.UrlFormattingOption.RemoveFragment):
                return True
            try:
                open_link(url.toString())
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self.parent(), '无法打开链接', str(exc))
            return False
        return url.scheme() in ('data', 'about', 'file')


class MarkdownPreview(QWebEngineView):
    def __init__(self, text='', parent=None, base_dir=None):
        super().__init__(parent)
        self.preview_page = PreviewPage(self)
        self.setPage(self.preview_page)
        self.setAcceptDrops(False)
        self.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._ready = False
        self._html = ''
        self.base_dir = base_dir
        for key, action in [('Ctrl++', lambda: self.adjust_zoom(.1)), ('Ctrl+=', lambda: self.adjust_zoom(.1)), ('Ctrl+-', lambda: self.adjust_zoom(-.1)), ('Ctrl+0', lambda: self.setZoomFactor(1))]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(220)
        self.timer.timeout.connect(self._render)
        assets = Path(__file__).resolve().parent / 'assets/katex'
        options = {'throwOnError': False, 'trust': False, 'maxExpand': 1000,
            'delimiters': [{'left': '$$', 'right': '$$', 'display': True}, {'left': '$', 'right': '$', 'display': False},
                           {'left': '\\[', 'right': '\\]', 'display': True}, {'left': '\\(', 'right': '\\)', 'display': False}]}
        shell = '''<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src file: 'unsafe-inline'; style-src file: 'unsafe-inline'; font-src file: data:; img-src file: data:">
<link rel="stylesheet" href="katex.min.css">
<style>body{margin:0;padding:18px;color:#292a43;background:white;font:15px/1.8 'Segoe UI',sans-serif;overflow-wrap:anywhere}
h1{font-size:1.6em}h2{font-size:1.3em}h3{font-size:1.1em}h1,h2,h3{line-height:1.5;margin:1em 0 .45em}
p{margin:.65em 0}a{color:#7046d5;text-decoration:underline}table{border-collapse:collapse;max-width:100%}td,th{border:1px solid #ddd;padding:6px}
pre{white-space:pre-wrap;background:#f6f4fa;padding:12px;border-radius:8px}code{font-family:monospace}.katex{font-size:1.12em}.katex-display{overflow-x:auto;overflow-y:hidden;padding:8px 0}
blockquote{border-left:3px solid #c9b8ee;margin-left:0;padding-left:14px;color:#646781}</style></head>
<body><main id="content"></main><script src="katex.min.js"></script><script src="contrib/auto-render.min.js"></script><script>
window.updatePreview=function(markup){const y=window.scrollY;const content=document.getElementById('content');content.innerHTML=markup;MATH_SCRIPT;window.scrollTo(0,y);};
</script></body></html>'''.replace('MATH_SCRIPT', MATH_SCRIPT)
        shell = shell.replace('</style>', "html,body,#content{min-width:0;max-width:100%;box-sizing:border-box}img{display:block;max-width:100%;height:auto;margin:1em auto}table{display:block;overflow-x:auto}pre{overflow-x:auto}.math-source[data-display='true']{display:block;max-width:100%;overflow-x:auto;padding:8px 0}.katex-display{margin:.4em 0} </style>")
        self.loadFinished.connect(self._loaded)
        super().setHtml(shell, QUrl.fromLocalFile(str(assets) + '/'))
        self.setMarkdown(text)

    def _loaded(self, ok):
        self._ready = ok
        if self.focusProxy():
            self.focusProxy().installEventFilter(self)
        if ok: self._render()

    def setMarkdown(self, text):
        self.setHtml(markdown_html(text, self.base_dir))

    def adjust_zoom(self, amount):
        self.setZoomFactor(max(.5, min(3.0, round(self.zoomFactor() + amount, 2))))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                self.adjust_zoom(.1 if delta > 0 else -.1)
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def setHtml(self, markup, baseUrl=None):
        match = re.search(r'<body[^>]*>([\s\S]*)</body>', markup)
        self._html = match.group(1) if match else markup
        self.timer.start()

    def _render(self):
        if self._ready:
            self.page().runJavaScript('window.updatePreview(' + json.dumps(self._html) + ');')


def markdown_view(text, minimum=160, base_dir=None):
    if re.search(r'\$|\\\[|\\\(|\\begin|!\[|```(?:math|latex)', text):
        view = MarkdownPreview(text, base_dir=base_dir)
    else:
        view = QTextBrowser()
        view.setOpenLinks(False)
        def follow(url):
            try:
                open_link(url.toString())
            except (ValueError, OSError) as exc:
                QMessageBox.warning(view, '无法打开链接', str(exc))
        view.anchorClicked.connect(follow)
        view.setHtml(markdown_html(text))
        view.setStyleSheet('QTextBrowser { background:white; border:none; padding:0; }')
    view.setMinimumHeight(minimum)
    return view


def zoom_toolbar(*views):
    bar = QWidget()
    row = QHBoxLayout(bar)
    row.setContentsMargins(0, 0, 0, 0)
    value = QLabel('100%')
    def change(delta=None):
        factor = 1.0 if delta is None else max(.5, min(3.0, round(views[0].zoomFactor() + delta, 2)))
        for view in views:
            view.setZoomFactor(factor)
        value.setText(f'{factor:.0%}')
    row.addWidget(button('−', lambda: change(-.1), 'ghost'))
    row.addWidget(value)
    row.addWidget(button('+', lambda: change(.1), 'ghost'))
    row.addWidget(button('重置缩放', lambda: change(), 'ghost'))
    timer = QTimer(bar)
    timer.timeout.connect(lambda: value.setText(f'{views[0].zoomFactor():.0%}'))
    timer.start(200)
    row.addStretch()
    return bar
