"""Built-in Markdown reading, editing and bilingual document viewing."""
import json
import queue
import shutil
import threading
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QSplitter, QWidget,
    QComboBox, QSpinBox, QFileDialog, QMessageBox, QCheckBox, QApplication)
from markdown_ui import MarkdownEdit, MarkdownPreview, editor_toolbar, zoom_toolbar
from document_markdown import prepare_document, translate_document, translation_current, atomic_text, partial_translation
from ui_theme import label, button


class MarkdownWindow(QDialog):
    def __init__(self, text='', parent=None, base_dir=None, title='内置 Markdown 编辑器', on_apply=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1050, screen.width() - 48), min(740, screen.height() - 60))
        layout = QVBoxLayout(self)
        mode = QComboBox()
        mode.addItems(['Markdown 源码与预览', '只编辑 Markdown', '只看排版'])
        layout.addWidget(mode)
        self.editor = MarkdownEdit(text)
        self.preview = MarkdownPreview(text, base_dir=base_dir)
        layout.addWidget(editor_toolbar(lambda: self.editor))
        layout.addWidget(zoom_toolbar(self.preview))
        split = QSplitter()
        split.addWidget(self.editor)
        split.addWidget(self.preview)
        split.setSizes([500, 550])
        layout.addWidget(split, 1)
        self.editor.textChanged.connect(lambda: self.preview.setMarkdown(self.editor.toPlainText()))
        mode.currentIndexChanged.connect(lambda index: (self.editor.setVisible(index != 2), self.preview.setVisible(index != 1)))
        row = QHBoxLayout()
        def open_md():
            path, _ = QFileDialog.getOpenFileName(self, '打开 Markdown', '', 'Markdown (*.md *.markdown *.txt)')
            if path:
                from document_markdown import read_text
                self.preview.base_dir = Path(path).parent
                self.editor.setPlainText(read_text(path))
        def save():
            path, _ = QFileDialog.getSaveFileName(self, '另存为 Markdown', '文档.md', 'Markdown (*.md)')
            if path:
                try:
                    atomic_text(path, self.editor.toPlainText())
                except OSError as exc:
                    QMessageBox.warning(self, '保存失败', str(exc))
        row.addWidget(button('打开 Markdown…', open_md, 'soft'))
        row.addWidget(button('另存为 Markdown…', save, 'primary'))
        row.addStretch()
        if on_apply:
            def apply_changes():
                on_apply(self.editor.toPlainText())
                self.close()
            row.addWidget(button('应用到编辑内容', apply_changes, 'primary'))
        row.addWidget(button('最大化 / 还原', lambda: self.showNormal() if self.isMaximized() else self.showMaximized()))
        row.addWidget(button('关闭', self.close))
        layout.addLayout(row)


class DocumentReader(QDialog):
    def __init__(self, paper, settings, root, parent=None):
        super().__init__(parent)
        self.paper, self.config, self.root = paper, settings, Path(root)
        self.is_pdf = Path(paper['path']).suffix.lower() == '.pdf'
        self.pdf = None
        self.pdf_page = 1
        self.page_translations = {}
        self.folder = None
        self.busy = self.dirty = False
        self._close_ready = self._flushing_close = self._close_after_work = False
        self.events = queue.Queue()
        self.cancelled = threading.Event()
        self.setWindowTitle('中英对照 · ' + paper['title'])
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1300, screen.width() - 48), min(850, screen.height() - 60))
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(['阅读原始 PDF' if self.is_pdf else '阅读原文', '阅读中文', '中英对照', 'Markdown 编辑与预览'])
        self.target = QComboBox()
        self.target.addItems(['原文.md', '中文.md', '中英对照.md'])
        self.target.setVisible(False)
        row.addWidget(self.mode)
        row.addWidget(self.target)
        row.addStretch()
        layout.addLayout(row)
        row = QHBoxLayout()
        self.translate_button = button('生成全文中文 / 对照', self.translate, 'primary')
        row.addWidget(self.translate_button)
        self.save_button = button('保存 Markdown', self.save, 'soft')
        row.addWidget(self.save_button)
        self.export_button = button('导出 Markdown 与图片…', self.export, 'soft')
        row.addWidget(self.export_button)
        self.save_chinese_button = button('保存中文译文…', self.save_chinese, 'soft')
        row.addWidget(self.save_chinese_button)
        self.cancel_button = button('停止翻译', lambda: (self.cancelled.set(), self.status.setText('将在当前批次完成或进入重试等待时停止；已完成内容会保留。')), 'ghost')
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.status = label('正在转换全文…', 'muted', True)
        layout.addWidget(self.status)
        self.side_hint = label('左侧 PDF 可连续滚动，右侧跟随当前页；中文滚动到底部后继续滚动可进入下一页' if self.is_pdf else '左侧：原文 Markdown　｜　右侧：中文 Markdown', 'muted', True)
        layout.addWidget(self.side_hint)
        self.editor = MarkdownEdit()
        from reading_widgets import AnnotatedPreview
        self.source = AnnotatedPreview('英文原文', self.persist_notes)
        self.chinese = AnnotatedPreview('中文译文', self.persist_notes)
        self.summary = MarkdownPreview(paper.get('summary') or '暂无总结')
        self.page_bar = QWidget()
        page_row = QHBoxLayout(self.page_bar)
        page_row.setContentsMargins(0, 0, 0, 0)
        self.page_number = QSpinBox()
        self.page_number.setPrefix('第 ')
        if self.is_pdf:
            from pdf_reader import PdfPane
            self.pdf = PdfPane(paper['path'], self.persist_notes)
            self.page_number.setRange(1, max(1, self.pdf.document.pageCount()))
            self.page_number.setSuffix(f' / {self.pdf.document.pageCount()} 页')
            self.pdf.page_changed.connect(self.show_pdf_page)
        self.page_number.valueChanged.connect(self.show_pdf_page)
        if self.is_pdf:
            self.chinese.boundary_scroll = lambda step: self.page_number.setValue(self.page_number.value()+step)
        self.page_number.installEventFilter(self)
        page_row.addWidget(button('上一页', lambda: self.page_number.setValue(self.page_number.value()-1)))
        page_row.addWidget(self.page_number)
        page_row.addWidget(button('下一页', lambda: self.page_number.setValue(self.page_number.value()+1)))
        if self.pdf:
            page_row.addWidget(button('PDF 适应宽度', self.pdf.fit_width, 'soft'))
        page_row.addStretch()
        self.page_bar.setVisible(self.is_pdf)
        layout.addWidget(self.page_bar)
        controls = QHBoxLayout()
        controls.addWidget(zoom_toolbar(self.pdf or self.source, self.chinese, self.summary))
        show_summary = QCheckBox('显示文献总结')
        controls.addWidget(show_summary)
        self.sync = QCheckBox('对照同步滚动')
        self.sync.setChecked(False)
        controls.addWidget(self.sync)
        self.source_note_button = button('原文便签 +', (self.pdf or self.source).add_note, 'soft')
        self.chinese_note_button = button('中文便签 +', self.chinese.add_note, 'soft')
        controls.addWidget(self.source_note_button)
        controls.addWidget(self.chinese_note_button)
        layout.addLayout(controls)
        self.edit_tools = editor_toolbar(lambda: self.editor)
        layout.addWidget(self.edit_tools)
        self.split = QSplitter()
        for widget in ([self.editor, self.pdf, self.source, self.chinese, self.summary] if self.pdf else [self.editor, self.source, self.chinese, self.summary]):
            widget.setMinimumWidth(0)
            self.split.addWidget(widget)
        self.split.setSizes([0, 700, 0, 0])
        layout.addWidget(self.split, 1)
        self.summary.hide()
        show_summary.toggled.connect(self.summary.setVisible)
        self.mode.setCurrentIndex(2)
        self.mode.currentIndexChanged.connect(self.change_mode)
        self.target.currentIndexChanged.connect(self.change_target)
        self.editor.textChanged.connect(self.edited)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(150)
        self.scroll_timer = QTimer(self)
        self.scroll_timer.timeout.connect(self.sync_scroll)
        if not self.is_pdf:
            self.scroll_timer.start(250)
        self._scrolling = False
        self._target_name = '原文.md'
        self._last_mode = 0
        self.change_mode()
        self.run(lambda: prepare_document(paper['path'], self.root, self.progress, page_snapshots=False), 'prepared')

    def progress(self, message):
        self.events.put(('status', message))

    def run(self, job, kind):
        self.busy = True
        self.update_controls()
        def worker():
            try:
                self.events.put((kind, job()))
            except Exception as exc:
                self.events.put(('error', str(exc) if isinstance(exc, (ValueError, RuntimeError, OSError)) else '文档处理失败，请检查格式与文件内容。'))
        threading.Thread(target=worker, daemon=True).start()

    def update_controls(self):
        ready = self.folder is not None and not self.busy
        self.cancel_button.setVisible(self.folder is not None and self.busy)
        for widget in (self.translate_button, self.save_button, self.export_button, self.mode, self.target):
            widget.setEnabled(ready)
        self.editor.setReadOnly(self.busy)
        self.save_chinese_button.setEnabled(self.folder is not None)
        self.source_note_button.setEnabled(self.folder is not None)
        self.chinese_note_button.setEnabled(self.folder is not None)

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'status':
                self.status.setText(value)
                continue
            if kind == 'batch':
                self.load_views()
                self.status.setText(f'已显示 {value["label"]} · {value["completed"]}/{value["total"]} 批已保存，继续翻译…')
                continue
            self.busy = False
            if kind == 'error':
                self.load_views()
                self.status.setText(value)
            elif kind == 'prepared':
                self.folder = Path(value)
                self.restore_notes()
                self.source.base_dir = self.chinese.base_dir = self.folder
                info = json.loads((self.folder / 'document.json').read_text('utf-8'))
                self.status.setText(('PDF 已就绪，中文按页码对应。' if self.is_pdf else '全文 Markdown 已就绪。') + (' '.join(info.get('warnings', [])) or '图片保留在 assets 文件夹；图片内文字未单独翻译。'))
                self.load_views()
            else:
                self.status.setText('已读取缓存译文，没有请求模型。' if value['cached'] else f'全文翻译完成 · {value["calls"]} 次翻译请求 · 原文、中文、对照及图片已保存。')
                self.load_views()
                self.mode.setCurrentIndex(2)
            self.update_controls()
            if self._close_after_work:
                QTimer.singleShot(0, self.close)

    def load_views(self):
        if not self.folder:
            return
        if self.is_pdf:
            from pdf_translation import read_page_translations
            self.page_translations = read_page_translations(self.folder)
            self.show_pdf_page(self.pdf_page)
            return
        partial = partial_translation(self.folder)
        source = self.folder / ('原文.翻译中.md' if partial and (self.folder / '原文.翻译中.md').exists() else '原文.md')
        self.source.setMarkdown(source.read_text('utf-8'))
        chinese = self.folder / ('中文.翻译中.md' if partial else '中文.md')
        self.chinese.setMarkdown(chinese.read_text('utf-8') if chinese.exists() else '尚未生成中文译文。点击“生成全文中文 / 对照”。')
        if partial:
            self.status.setText(f'已恢复 {partial["completed"]}/{partial["total"]} 批译文，点击生成继续。')
        elif chinese.exists() and not translation_current(self.folder):
            self.status.setText('原文或译文已编辑，现有对照可能过期；点击生成可更新。')

    def save(self):
        if self.folder and not self.busy:
            try:
                atomic_text(self.folder / self._target_name, self.editor.toPlainText())
                self.dirty = False
                self.status.setText('已保存 ' + self._target_name)
                return True
            except OSError as exc:
                QMessageBox.warning(self, '保存失败', str(exc))
        return False

    def change_target(self):
        if self.dirty and not self.save():
            self.target.blockSignals(True)
            self.target.setCurrentText(self._target_name)
            self.target.blockSignals(False)
            return
        self._target_name = self.target.currentText()
        self.editor.blockSignals(True)
        path = self.folder / self._target_name if self.folder else None
        self.editor.setPlainText(path.read_text('utf-8') if path and path.exists() else '')
        self.editor.blockSignals(False)
        self.dirty = False
        self.source.setMarkdown(self.editor.toPlainText())

    def edited(self):
        self.dirty = True
        self.source.setMarkdown(self.editor.toPlainText())

    def change_mode(self):
        if self.dirty and not self.save():
            self.mode.blockSignals(True)
            self.mode.setCurrentIndex(self._last_mode)
            self.mode.blockSignals(False)
            return
        index = self.mode.currentIndex()
        self._last_mode = index
        editing = index == 3
        self.editor.setVisible(editing)
        self.source.setVisible(editing if self.is_pdf else index != 1)
        if self.pdf:
            self.pdf.setVisible(index in (0, 2))
        self.page_bar.setVisible(self.is_pdf and not editing)
        self.chinese.setVisible(index in (1, 2))
        self.target.setVisible(editing)
        self.edit_tools.setVisible(editing)
        self.save_button.setVisible(editing)
        self.sync.setVisible(index == 2 and not self.is_pdf)
        self.side_hint.setVisible(index == 2)
        if editing:
            self.change_target()
        else:
            self.load_views()
        self.split.setSizes(([0, 650, 0, 650, 0] if not editing else [550, 0, 650, 0, 0]) if self.pdf else [550 if editing else 0, 650, 650 if index in (1, 2) else 0, 320 if self.summary.isVisible() else 0])

    def translate(self):
        if self.dirty and not self.save():
            return
        if self.folder and not self.busy:
            if self.parent() and hasattr(self.parent(), 'library'):
                self.config = self.parent().library.settings()
            if self.pdf:
                self.config = {**self.config, 'pdf_page_count': self.pdf.document.pageCount()}
            self.mode.setCurrentIndex(2)
            self.cancelled.clear()
            self.run(lambda: translate_document(self.folder, self.config, self.progress, cancelled=self.cancelled.is_set, on_batch=lambda info: self.events.put(('batch', info))), 'translated')

    def save_chinese(self):
        if self.dirty and not self.save():
            return
        if not self.folder:
            return
        if self.is_pdf:
            from pdf_translation import read_page_translations
            pages = read_page_translations(self.folder)
            text = '\n\n'.join(f'## 第 {n} 页\n\n{pages[n]}' for n in sorted(pages))
        else:
            path = self.folder / ('中文.翻译中.md' if partial_translation(self.folder) else '中文.md')
            text = path.read_text('utf-8') if path.exists() else ''
        if not text.strip():
            QMessageBox.information(self, '尚无译文', '请先生成中文译文，完成的部分会自动保存。')
            return
        target, _ = QFileDialog.getSaveFileName(self, '保存已完成的中文译文', '中文译文.md', 'Markdown (*.md)')
        if not target:
            return
        try:
            dest = Path(target)
            if not dest.suffix:
                dest = dest.with_suffix('.md')
            assets = self.folder / 'assets'
            if assets.is_dir() and any(assets.iterdir()):
                image_dir = dest.with_name(dest.stem + '_图片')
                number = 2
                while image_dir.exists():
                    image_dir = dest.with_name(dest.stem + f'_图片_{number}')
                    number += 1
                shutil.copytree(assets, image_dir)
                text = text.replace('(assets/', '(' + image_dir.name + '/')
            atomic_text(dest, text)
            self.status.setText('中文译文已保存：' + str(dest))
        except OSError as exc:
            QMessageBox.warning(self, '保存失败', str(exc))

    def export(self):
        if self.dirty and not self.save():
            return
        directory = QFileDialog.getExistingDirectory(self, '选择导出目录（将新建独立文件夹）')
        if directory and self.folder:
            try:
                stem = 'Markdown文献'
                dest = Path(directory) / stem
                number = 2
                while dest.exists():
                    dest = Path(directory) / f'{stem}-{number}'
                    number += 1
                dest.mkdir()
                for name in ('原文.md', '中文.md', '中英对照.md', '中文.翻译中.md', '中英对照.翻译中.md', '中文分页.json', '批注.json'):
                    if (self.folder / name).exists():
                        shutil.copy2(self.folder / name, dest / name)
                shutil.copytree(self.folder / 'assets', dest / 'assets')
                self.status.setText('已导出：' + str(dest))
            except OSError as exc:
                QMessageBox.warning(self, '导出失败', str(exc))

    def eventFilter(self, watched, event):
        if watched is getattr(self, 'page_number', None) and event.type() == QEvent.Type.Wheel:
            event.ignore()
            return True
        return super().eventFilter(watched, event)

    def show_pdf_page(self, number):
        if not self.pdf:
            return
        number = max(1, min(number, max(1, self.pdf.document.pageCount())))
        changed = number != self.pdf_page
        self.pdf_page = number
        self.page_number.blockSignals(True)
        self.page_number.setValue(number)
        self.page_number.blockSignals(False)
        if self.pdf.current != number:
            self.pdf.set_page(number)
        self.chinese.set_note_page(number)
        text = self.page_translations.get(number)
        if text is None:
            text = '这一页尚未完成翻译。点击生成后，译文将按原 PDF 页码显示。已有旧译文不会用于猜测页码，请生成逐页译文。'
        self.chinese.setMarkdown(f'## 第 {number} 页\n\n{text}')
        if changed:
            self.chinese.page().runJavaScript('window.scrollTo(0,0)')

    def restore_notes(self):
        try:
            notes = json.loads((self.folder / '批注.json').read_text('utf-8'))
        except (OSError, ValueError):
            notes = {}
        for view in ([self.source, self.pdf, self.chinese] if self.pdf else [self.source, self.chinese]):
            saved = notes.get(view.note_side, [])
            known = {n.get('id') for n in saved}
            view.restore_notes(saved + [n for n in view.notes if n.get('id') not in known])

    def persist_notes(self):
        if self.folder:
            try:
                atomic_text(self.folder / '批注.json', json.dumps({v.note_side: v.notes for v in ([self.source, self.pdf, self.chinese] if self.pdf else [self.source, self.chinese])}, ensure_ascii=False))
                return True
            except OSError:
                self.status.setText('便签保存失败，请检查文档目录写入权限。')
                return False
        return True

    def sync_scroll(self):
        if self.mode.currentIndex() != 2 or not self.sync.isChecked() or self._scrolling:
            return
        from PySide6.QtGui import QCursor
        active = self.chinese if self.chinese.rect().contains(self.chinese.mapFromGlobal(QCursor.pos())) else self.source
        other = self.source if active is self.chinese else self.chinese
        if not active._ready or not other._ready:
            return
        self._scrolling = True
        def apply(ratio):
            from shiboken6 import isValid
            if not isValid(self) or not isValid(other):
                return
            if isinstance(ratio, (float, int)) and 0 <= ratio <= 1:
                other.page().runJavaScript(f'window.scrollTo(0,{ratio} * Math.max(0,document.documentElement.scrollHeight-innerHeight))')
            self._scrolling = False
        active.page().runJavaScript('scrollY / Math.max(1,document.documentElement.scrollHeight-innerHeight)', apply)

    def closeEvent(self, event):
        if self._close_ready:
            self.scroll_timer.stop()
            self.timer.stop()
            if self.pdf:
                self.pdf.view.setDocument(None)
                self.pdf.document.close()
            super().closeEvent(event)
            return
        event.ignore()
        if self.busy:
            self._close_after_work = True
            self.cancelled.set()
            self.status.setText('正在保存已完成译文和便签；当前请求结束后将关闭。')
            return
        if self.dirty and not self.save():
            return
        if self._flushing_close:
            return
        self._flushing_close = True
        self._pending_flush = 2
        def completed():
            self._pending_flush -= 1
            if self._pending_flush:
                return
            self._flushing_close = False
            if self.persist_notes():
                self._close_ready = True
                QTimer.singleShot(0, self.close)
            else:
                self._close_after_work = False
                QMessageBox.warning(self, '便签未保存', '保存失败，窗口仍然保留。请检查目录写入权限后重试。')
        self.source.flush_notes(completed)
        self.chinese.flush_notes(completed)
