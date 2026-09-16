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
    deadline = time.monotonic() + 15
    while not view._ready and time.monotonic() < deadline:
        QTest.qWait(30)
    if not view._ready: raise AssertionError('Preview page did not finish loading')
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
    def test_reader_keeps_edited_bilingual_markdown_and_notes(self):
        from reader_ui import DocumentReader
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'paper.md'
            source.write_text('English original $x$', 'utf-8')
            paper = {'id': 1, 'title': 'A paper', 'path': str(source), 'summary': 'Summary'}
            reader = DocumentReader(paper, {}, root / 'cache')
            reader.show()
            deadline = time.monotonic() + 15
            while reader.busy and time.monotonic() < deadline:
                QTest.qWait(50)
            try:
                self.assertFalse(reader.busy)
                self.assertEqual(reader.mode.currentIndex(), 2)
                reader.mode.setCurrentIndex(3)
                reader.target.setCurrentText('中文.md')
                reader.editor.setPlainText('人工校订中文 $x$')
                self.assertTrue(reader.save())
                reader.mode.setCurrentIndex(2)
                wait_render(reader.chinese, 'document.body.innerText.includes("人工校订中文")')
                wait_render(reader.source, '!!window.noteBridge')
                reader.source.add_note()
                wait_render(reader.source, 'document.querySelectorAll(".paper-note").length === 1')
                javascript(reader.source, 'noteItems[0].text="待核对";noteItems[0].x=23;noteItems[0].y=240;saveNotes();')
                QTest.qWait(300)
                notes = json.loads((reader.folder / '批注.json').read_text('utf-8'))
                self.assertEqual(notes['英文原文'][0]['text'], '待核对')
                reader.restore_notes()
                self.assertEqual(reader.source.notes[0]['y'], 240)
                self.assertEqual((reader.folder / '中文.md').read_text('utf-8'), '人工校订中文 $x$')
            finally:
                reader.close()
                QTest.qWait(200)

    def test_wheel_scrolls_outer_continuous_preview(self):
        from reading_widgets import SectionPreview
        from PySide6.QtWidgets import QScrollArea, QWidget, QVBoxLayout
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        outer = QScrollArea()
        outer.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        preview = SectionPreview([('章节', '\n\n'.join(['Paragraph']*100))], expand_height=True)
        layout.addWidget(preview)
        outer.setWidget(body)
        outer.resize(500, 350)
        outer.show()
        try:
            wait_render(preview, 'document.querySelectorAll("p").length > 50')
            QTest.qWait(500)
            event = QWheelEvent(QPointF(40,40), QPointF(40,40), QPoint(), QPoint(0,-120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QAPP.sendEvent(preview.focusProxy(), event)
            self.assertGreater(outer.verticalScrollBar().value(), 0)
        finally:
            outer.close()

    def test_continuous_sections_collapse_and_note_bridge(self):
        from reading_widgets import SectionPreview, AnnotatedPreview
        view = SectionPreview([('研究总结', '\n\n'.join(['Long paragraph'] * 60)), ('摘要', '$x_i$')], expand_height=True)
        view.resize(500, 400)
        view.show()
        try:
            wait_render(view, 'document.querySelectorAll("details").length === 2 && document.querySelector(".katex") !== null')
            QTest.qWait(500)
            expanded = view.height()
            javascript(view, 'document.querySelector("summary").click()')
            QTest.qWait(600)
            self.assertLess(view.height(), expanded - 300)
        finally:
            view.close()
        changed = []
        notes = AnnotatedPreview('英文原文', lambda: changed.append(True))
        notes.resize(600, 500)
        notes.show()
        try:
            wait_render(notes, '!!window.noteBridge')
            notes.add_note()
            wait_render(notes, 'document.querySelectorAll(".paper-note").length === 1')
            javascript(notes, 'const t=document.querySelector("textarea");t.value="需要核对公式";t.dispatchEvent(new Event("input"));noteItems[0].x=40;noteItems[0].y=320;saveNotes();')
            QTest.qWait(300)
            self.assertTrue(changed)
            self.assertEqual(notes.notes[0]['text'], '需要核对公式')
            self.assertEqual(notes.notes[0]['y'], 320)
            saved = notes.notes.copy()
            notes.restore_notes(saved)
            self.assertEqual(javascript(notes, 'parseInt(document.querySelector(".paper-note").style.left)'), 40)
            notes.setMarkdown('Edited **Markdown** $x$')
            QTest.qWait(400)
            self.assertEqual(javascript(notes, 'document.querySelectorAll(".paper-note").length'), 1)
        finally:
            notes.close()

    def test_multiline_math_code_images_and_zoom(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new('RGB', (800, 300), 'red').save(root / 'figure.png')
            text = '![图片](figure.png)\n\n$$\n\\begin{aligned}a&=b+c\\\\d&=e\\end{aligned}\n$$\n\n```math\n\\frac{1}{1+x}\n```\n\n`$not_math$`\n\n\\(x_i^2\\)'
            view = MarkdownPreview(text, base_dir=root)
            view.resize(500, 500)
            view.show()
            try:
                wait_render(view, 'document.querySelectorAll(".katex").length === 3 && document.querySelector("#content img").naturalWidth > 0')
                self.assertEqual(javascript(view, 'document.querySelectorAll(".katex-error").length'), 0)
                self.assertIn('$not_math$', javascript(view, 'document.body.innerText'))
                for scale in (.5, 1.0, 1.75, 3.0, 1.0):
                    view.setZoomFactor(scale)
                    QTest.qWait(100)
                    self.assertAlmostEqual(view.zoomFactor(), scale, places=2)
                    self.assertEqual(javascript(view, 'document.querySelectorAll(".katex").length'), 3)
                    self.assertTrue(javascript(view, 'document.querySelector("img").getBoundingClientRect().width <= innerWidth'))
                view.resize(280, 500)
                QTest.qWait(100)
                self.assertTrue(javascript(view, 'document.documentElement.scrollWidth <= innerWidth + 1'))
            finally:
                view.close()
                view.deleteLater()
                QAPP.processEvents()

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
                if dialog is None:
                    QTimer.singleShot(50, edit)
                    return
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
                    expand = next(b for b in dialog.findChildren(QPushButton) if '在大窗口中编辑' in b.text())
                    expand.click()
                    from reader_ui import MarkdownWindow
                    large = dialog.findChild(MarkdownWindow)
                    self.assertTrue(large.isMaximized())
                    large.editor.setPlainText(entry.toPlainText() + '\n\n大窗口回填内容')
                    apply_button = next(b for b in large.findChildren(QPushButton) if '应用到编辑内容' in b.text())
                    apply_button.click()
                    self.assertIn('大窗口回填内容', entry.toPlainText())
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
