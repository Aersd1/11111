import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from library_core import Library
from markdown_ui import MarkdownEdit, MarkdownPreview
from PySide6.QtWidgets import QApplication, QTabWidget, QSplitter, QDialog
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtTest import QTest

QAPP = QApplication.instance() or QApplication([])


def javascript(view, expression):
    result = []
    view.page().runJavaScript(expression, lambda value: result.append(value))
    deadline = time.monotonic() + 5
    while not result and time.monotonic() < deadline:
        QTest.qWait(20)
    if not result: raise AssertionError('JavaScript callback timed out')
    return result[0]


def wait_render(view, expression):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        QTest.qWait(80)
        if javascript(view, expression): return
    raise AssertionError('Live formula rendering timed out')


class LiveMarkdownTests(unittest.TestCase):
    def test_real_typesetting_updates_and_safe_links(self):
        view = MarkdownPreview('**重点** *斜体* $x_i^2$\n\n$$\\frac{a}{b}$$\n\n[论文](https://example.org/paper)')
        view.resize(680, 600)
        view.show()
        try:
            wait_render(view, 'document.querySelectorAll(".katex").length === 2')
            self.assertTrue(javascript(view, 'document.querySelector(".katex-mathml math") !== null'))
            self.assertEqual(javascript(view, 'document.querySelector("#content a").href'), 'https://example.org/paper')
            self.assertFalse(javascript(view, 'document.body.innerText.includes("$$")'))
            view.setMarkdown('old $a$')
            view.setMarkdown('最新 $$\\sum_{i=1}^{n}i^2$$')
            wait_render(view, 'document.querySelectorAll(".katex").length === 1 && document.body.innerText.includes("最新")')
            view.setMarkdown('<script>window.compromised=true</script> [x](javascript:alert(1))')
            wait_render(view, 'document.querySelectorAll(".katex").length === 0')
            self.assertFalse(javascript(view, 'window.compromised === true'))
            self.assertFalse(javascript(view, '!!document.querySelector("#content a[href^=javascript]")'))
            with patch('markdown_ui.open_link') as opened:
                accepted = view.page().acceptNavigationRequest(QUrl('https://example.org/paper'), view.page().NavigationType.NavigationTypeLinkClicked, True)
            self.assertFalse(accepted)
            opened.assert_called_once_with('https://example.org/paper')
        finally:
            view.close()
            view.deleteLater()
            QAPP.processEvents()

    def test_link_insertion_escapes_labels_and_local_paths(self):
        editor = MarkdownEdit('选中文字')
        editor.selectAll()
        editor.insert_link('研究 [附件]', 'https://example.org/a(b)?q=hello world')
        self.assertIn('研究 \\[附件\\]', editor.toPlainText())
        self.assertIn('a%28b%29?q=hello%20world', editor.toPlainText())
        with self.assertRaises(ValueError):
            editor.insert_link('bad', 'javascript:alert(1)')
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / '附件 with space.txt'
            source.write_text('data')
            editor.setPlainText('')
            editor.insert_link('附件', str(source))
            self.assertIn('file:///', editor.toPlainText())
            self.assertIn('%20', editor.toPlainText())

    def test_description_split_preview_and_save(self):
        from app import App
        with tempfile.TemporaryDirectory() as temp:
            lib = Library(Path(temp) / 'library.sqlite3')
            source = Path(temp) / 'paper.txt'
            source.write_text('paper')
            ident, _ = lib.add(source)
            window = App(lib)
            window.show()
            QAPP.processEvents()
            window.table.selectRow(0)
            errors = []
            def edit():
                dialog = QAPP.activeModalWidget()
                try:
                    tabs = dialog.findChild(QTabWidget)
                    preview = dialog.findChild(MarkdownPreview)
                    split = dialog.findChild(QSplitter)
                    self.assertEqual(split.count(), 2)
                    index = next(i for i in range(tabs.count()) if tabs.tabText(i) == '个人描述')
                    tabs.setCurrentIndex(index)
                    entry = tabs.currentWidget()
                    entry.setPlainText('描述 **重点** $$\\frac{1}{2}$$\n\n[资料](https://example.org)')
                    wait_render(preview, 'document.querySelectorAll(".katex").length === 1 && document.body.innerText.includes("描述")')
                    from PySide6.QtWidgets import QPushButton
                    save = next(b for b in dialog.findChildren(QPushButton) if '保存修改' in b.text())
                    save.click()
                except Exception as exc:
                    errors.append(exc)
                    dialog.reject()
            QTimer.singleShot(100, edit)
            window.edit_dialog()
            window.close()
            window.deleteLater()
            QAPP.processEvents()
            if errors: raise errors[0]
            self.assertIn('$$\\frac{1}{2}$$', lib.get(ident)['description'])
            self.assertIn('[资料](https://example.org)', lib.get(ident)['description'])


if __name__ == '__main__': unittest.main()
