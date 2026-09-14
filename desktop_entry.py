"""Release entry point. No dependencies or network setup needed at launch."""
import os
import sys
from pathlib import Path


def main():
    smoke = '--smoke-test' in sys.argv
    if smoke:
        target = Path(sys.argv[sys.argv.index('--smoke-test') + 1]).resolve()
        target.mkdir(parents=True, exist_ok=True)
        os.environ['LITERATURE_DATA_DIR'] = str(target)
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
    from app import App
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication, QMessageBox
    from ui_theme import setup_application
    from platform_support import data_root
    application = QApplication(sys.argv)
    setup_application(application)
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(root / '.app.lock'))
    if not lock.tryLock(100):
        if not smoke:
            QMessageBox.information(None, '文献书架已在运行', '请切换到已经打开的文献书架窗口。')
        return 2
    window = App()
    if smoke:
        import json
        from library_core import extract
        from pypdf import PdfWriter
        from local_search import BM25Index
        pdf = root / 'smoke.pdf'
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        writer.write(str(pdf))
        extract(pdf)
        source = root / 'smoke.txt'
        source.write_text('battery energy prediction', 'utf-8')
        ident, _ = window.library.add(source)
        window.library.update(ident, title='battery energy prediction')
        window.library.assign_category([ident], '测试大类', '测试小类')
        window.refresh()
        index = BM25Index()
        index.update(window.library.all())
        assert index.search('battery', 10)
        from markdown_ui import markdown_html, MarkdownEdit
        from fulltext_agent import extract_full
        assert 'battery' in extract_full(source)[0]
        assert '$x_i$' in markdown_html('**bold** $x_i$')
        assert (Path(__file__).resolve().parent / 'assets/katex/katex.min.js').is_file()
        editor = MarkdownEdit('test')
        editor.selectAll()
        editor.wrap('**')
        assert editor.toPlainText() == '**test**'
        from markdown_ui import MarkdownPreview
        import time
        preview = MarkdownPreview('**Formula** $$\\frac{a}{b}$$')
        preview.show()
        rendered = []
        deadline = time.monotonic() + 25
        while not any(rendered) and time.monotonic() < deadline:
            if preview._ready:
                preview.page().runJavaScript('document.querySelectorAll(".katex").length === 1', lambda value: rendered.append(bool(value)))
            for _ in range(10):
                application.processEvents()
                time.sleep(.02)
        assert any(rendered), 'Packaged offline formula renderer failed'
        preview.close()
        preview.deleteLater()
        window.show()
        application.processEvents()
        assert window.grab().save(str(root / 'smoke.png'))
        window.close()
        (root / 'smoke-result.json').write_text(json.dumps({'ok': True, 'frozen': bool(getattr(sys, 'frozen', False)),
            'platform': sys.platform, 'pdf': True, 'search': True, 'folders': True}), 'utf-8')
        return 0
    screen = application.primaryScreen().availableGeometry()
    window.resize(min(1510, screen.width() - 32), min(925, screen.height() - 45))
    window.show()
    return application.exec()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        from platform_support import data_root
        path = data_root() / 'startup-error.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(traceback.format_exc(), 'utf-8')
        if '--smoke-test' in sys.argv:
            raise SystemExit(1)
        raise
