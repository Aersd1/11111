"""Native PDF pane with one explicit page and page-scoped movable notes."""
import uuid
from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton


class Sticky(QWidget):
    def __init__(self, pane, note):
        super().__init__(pane.view.viewport())
        self.pane, self.note, self.drag = pane, note, None
        self.setObjectName('pdfSticky')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet('QWidget#pdfSticky{background:#fff2aa;color:#443b20;border:1px solid #dcc56b;border-radius:6px;} QLabel,QPushButton{color:#443b20;background:transparent;border:0;} QPlainTextEdit{background:transparent;border:0;color:#443b20;font-size:14px;}')
        self.resize(195, 135)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 4, 5, 5)
        row = QHBoxLayout()
        title = QLabel('便签 · 拖动移动')
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(title)
        remove = QPushButton('×')
        remove.setFixedSize(23, 23)
        remove.setStyleSheet('QPushButton{background:#fff5c5;color:#443b20;border:1px solid #dcc56b;padding:0;min-width:0;min-height:0;border-radius:3px;font-size:16px;}')
        remove.clicked.connect(self.remove)
        row.addWidget(remove)
        layout.addLayout(row)
        editor = QPlainTextEdit(note.get('text', ''))
        self.editor = editor
        editor.setPlaceholderText('写下批注…')
        editor.textChanged.connect(lambda: self.save_text(editor.toPlainText()))
        layout.addWidget(editor)
        self.reposition()
        self.show()
        self.raise_()

    def reposition(self):
        area = self.pane.view.viewport()
        self.move(round(self.note.get('rx', .06)*max(1, area.width()-self.width())),
                  round(self.note.get('ry', .12)*max(1, area.height()-self.height())))

    def save_text(self, text):
        self.note['text'] = text
        self.pane.changed()

    def remove(self):
        self.pane.notes = [n for n in self.pane.notes if n['id'] != self.note['id']]
        self.hide()
        self.pane.changed()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 38:
            self.drag = event.globalPosition().toPoint() - self.pos()
            self.grabMouse()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag is not None:
            target = event.globalPosition().toPoint()-self.drag
            area = self.pane.view.viewport()
            x = max(0, min(target.x(), max(0, area.width()-self.width())))
            y = max(0, min(target.y(), max(0, area.height()-self.height())))
            self.move(x, y)
            self.note.update(rx=x/max(1,area.width()-self.width()), ry=y/max(1,area.height()-self.height()))

    def mouseReleaseEvent(self, event):
        if self.drag is not None:
            self.drag = None
            self.releaseMouse()
            self.pane.changed()


class PdfPane(QWidget):
    page_changed = Signal(int)

    def __init__(self, path, changed):
        super().__init__()
        self.note_side, self.changed, self.notes = 'PDF原文', changed, []
        self.widgets = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.document = QPdfDocument(self)
        self.view = QPdfView(self)
        self.view.setDocument(self.document)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        layout.addWidget(self.view)
        self.document.load(str(path))
        self.current = 1
        self.view.pageNavigator().currentPageChanged.connect(self.on_page)

    def on_page(self, index):
        if self.current == index+1:
            return
        self.current = index+1
        self.draw_notes()
        self.page_changed.emit(self.current)

    def set_page(self, number):
        if 1 <= number <= self.document.pageCount():
            self.view.pageNavigator().jump(number-1, QPointF())

    def zoomFactor(self):
        return self.view.zoomFactor()

    def setZoomFactor(self, value):
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.view.setZoomFactor(value)

    def fit_width(self):
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

    def restore_notes(self, notes):
        self.notes = notes
        self.draw_notes()

    def add_note(self):
        offset = sum(n.get('page', 1) == self.current for n in self.notes) % 5
        self.notes.append({'id': uuid.uuid4().hex, 'page': self.current, 'text': '', 'rx': max(.05, .9-offset*.06), 'ry': min(.8, .12+offset*.05)})
        self.draw_notes()
        self.changed()
        if self.widgets:
            self.widgets[-1].editor.setFocus()

    def draw_notes(self):
        for widget in self.widgets:
            widget.hide()
            widget.deleteLater()
        self.widgets = [Sticky(self, n) for n in self.notes if n.get('page', 1) == self.current]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for widget in self.widgets:
            widget.reposition()
