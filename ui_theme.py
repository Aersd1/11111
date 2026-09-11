from PySide6.QtCore import Qt, QSize, QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QLinearGradient, QPen, QFontDatabase, QFont
from pathlib import Path
import os
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout

STYLE = '''
QWidget { color: #22233b; font-family: "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", "Segoe UI", sans-serif; font-size: 14px; }
QMainWindow { background: #dee9f5; }
QWidget#shell { background: #f7f8fc; border-radius: 22px; }
QWidget#detailPage { background: white; }
QWidget#settingsPage { background: #f9faff; }
QFrame#rail { background: white; border-top-left-radius: 22px; border-bottom-left-radius: 22px; }
QFrame#sidebar { background: #fff; border-left: 1px solid #f0f1f6; border-right: 1px solid #edf0f7; }
QFrame#card, QFrame#detailCard { background: white; border: 1px solid #eceef5; border-radius: 18px; }
QLabel { background: transparent; border: none; }
QLabel#muted { color: #7c8098; font-size: 13px; }
QLabel#eyebrow { color: #7f859e; font-size: 12px; font-weight: 600; }
QLabel#heading { font-size: 25px; font-weight: 700; color: #202139; }
QLabel#sectionTitle { font-size: 17px; font-weight: 700; }
QLabel#brand { font-size: 19px; font-weight: 700; }
QLabel#badge { background: #f0ebff; color: #7041db; border-radius: 9px; padding: 5px 9px; font-size: 12px; }
QPushButton { border: 1px solid #e8e9f2; background: white; border-radius: 10px; padding: 9px 14px; font-weight: 500; }
QPushButton:hover { background: #f3f0ff; border-color: #d8cafa; color: #6534d7; }
QPushButton:pressed { background: #e7ddff; }
QPushButton:disabled { color: #a9adbb; background: #f5f6fa; border-color: #eceef5; }
QPushButton#primary { background: #7546ed; color: white; border: none; padding: 11px 19px; font-weight: 600; }
QPushButton#primary:hover { background: #6534da; }
QPushButton#primary:disabled { background: #c8b9ec; color: #fff; }
QPushButton#soft { background: #f0eaff; color: #7043d7; border: none; }
QPushButton#soft:checked { background: #7650dc; color: white; }
QPushButton#ghost { border: none; background: transparent; color: #7c8098; padding: 8px; }
QPushButton#ghost:hover { background: #f0ebff; color: #7041db; }
QPushButton#railButton { border: none; background: transparent; border-radius: 12px; padding: 12px; }
QPushButton#railButton:hover, QPushButton#railButton:checked { background: #efe8ff; }
QPushButton#navButton { text-align: left; background: transparent; border: none; border-radius: 10px; padding: 12px; }
QPushButton#navButton:checked { background: #efe9ff; color: #7143dd; font-weight: 600; }
QPushButton#danger { color: #be4b62; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #f9faff; border: 1px solid #e4e6f0; border-radius: 9px; padding: 10px 12px; selection-background-color: #ded3ff; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid #a288f1; background: white; }
QLineEdit#search { background: #f0f2f8; border: none; padding: 12px 16px; border-radius: 11px; }
QComboBox::drop-down { border: none; width: 23px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #ede7ff; selection-color: #6940d0; border: 1px solid #e5e7f0; padding: 5px; }
QTreeWidget { background: transparent; border: none; outline: none; font-size: 14px; }
QTreeWidget::item { padding: 10px 2px; border-radius: 8px; }
QTreeWidget::item:selected { background: #f0eaff; color: #7043d7; }
QTreeWidget::item:hover { background: #f6f3ff; }
QTreeWidget::branch { background: transparent; }
QTableWidget { background: white; border: none; gridline-color: transparent; selection-background-color: #f1ebff; selection-color: #322451; outline: none; }
QTableWidget::item { border-bottom: 1px solid #f0f1f7; padding: 9px; }
QTableWidget::item:selected { background: #f1ebff; color: #422c70; }
QHeaderView::section { background: #f8f9fd; color: #8a8fa5; border: none; padding: 12px 9px; font-size: 12px; text-align: left; }
QHeaderView { background: #f8f9fd; }
QTableCornerButton::section { background: #f8f9fd; border: none; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 4px 0; }
QScrollBar::handle:vertical { background: #d9dce9; min-height: 30px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { height: 7px; background: transparent; }
QScrollBar::handle:horizontal { background: #d9dce9; min-width: 30px; border-radius: 3px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QTextEdit, QPlainTextEdit { background: #fafbff; border: 1px solid #e4e7f1; border-radius: 9px; padding: 10px; selection-background-color: #ded3ff; }
QDialog { background: #f9faff; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab { background: transparent; color: #7d8199; padding: 12px 18px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #7346df; border-bottom: 2px solid #8053e8; font-weight: 600; }
QTabBar::tab:hover { color: #7041db; }
QProgressBar { border: none; background: #ece6fa; border-radius: 3px; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #8654ee; border-radius: 3px; }
QMenu { background: white; border: 1px solid #e6e8f0; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 10px 22px; border-radius: 6px; }
QMenu::item:selected { background: #eee7ff; color: #7041db; }
QSplitter::handle { background: #e8e7f1; border-radius: 4px; margin: 8px 3px; }
QSplitter::handle:hover { background: #b7a0eb; }
QSplitter::handle:pressed { background: #9368de; }
QToolTip { background: #292741; color: white; padding: 7px; border: none; }
QCheckBox { spacing: 8px; }
'''


def setup_application(application):
    # Explicit registration also makes offscreen renders use the real Chinese
    # typeface, rather than the offscreen plugin's empty font database.
    font_dir = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    for filename in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf'):
        path = font_dir / filename
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    application.setStyle('Fusion')
    application.setFont(QFont('Microsoft YaHei UI', 10))

PATHS = {
 'chevron_right': '<path d="m9 5 7 7-7 7"/>',
 'chevron_down': '<path d="m5 9 7 7 7-7"/>',
 'library': '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 3v18m4-13h4m-4 4h4"/>',
 'grid': '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
 'folder': '<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
 'spark': '<path d="m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7ZM20 2v4m-2-2h4"/>',
 'settings': '<path d="m9 3-1 3-3 1 1 3-2 2 2 2-1 3 3 1 1 3h6l1-3 3-1-1-3 2-2-2-2 1-3-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
 'plus': '<path d="M12 5v14M5 12h14"/>',
 'search': '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
 'open': '<path d="M14 3h7v7m0-7L11 13M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/>',
 'doc': '<path d="M14 3H5v18h14V8Zm0 0v5h5M8 12h8m-8 4h6"/>',
 'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
 'check': '<path d="m5 12 4 4L19 6"/>',
 'refresh': '<path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5M4 16a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
 'edit': '<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-4-4L5 15ZM4 20h16"/>',
 'download': '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
 'more': '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
 'x': '<path d="m6 6 12 12M6 18 18 6"/>',
}


def icon(name, color='#777d94', size=22):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{PATHS.get(name, PATHS["doc"])}</g></svg>'
    image = QPixmap(size * 2, size * 2)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(svg.encode()).render(painter)
    painter.end()
    image.setDevicePixelRatio(2)
    return QIcon(image)


def label(text='', role=None, wrap=False):
    widget = QLabel(str(text))
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        widget.setObjectName(role)
    widget.setWordWrap(wrap)
    return widget


def button(text='', callback=None, name='', glyph=None):
    widget = QPushButton(text)
    if name:
        widget.setObjectName(name)
    if glyph:
        widget.setIcon(icon(glyph, 'white' if name == 'primary' else '#8053df'))
        widget.setIconSize(QSize(18, 18))
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if callback:
        widget.clicked.connect(lambda checked=False: callback())
    return widget


class StatCard(QFrame):
    def __init__(self, title, subtitle, colors, glyph):
        super().__init__()
        self.colors = colors
        self.setMinimumHeight(145)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(23, 19, 23, 17)
        row = QHBoxLayout()
        self.caption = label(title)
        self.caption.setStyleSheet('color: white; font-size: 14px; font-weight: 500;')
        row.addWidget(self.caption)
        row.addStretch()
        glyph_label = QLabel()
        glyph_label.setPixmap(icon(glyph, 'white', 24).pixmap(24, 24))
        row.addWidget(glyph_label)
        layout.addLayout(row)
        self.number = label('0')
        self.number.setStyleSheet('color: white; font-size: 35px; font-weight: 700;')
        layout.addWidget(self.number)
        self.subtitle = label(subtitle)
        self.subtitle.setStyleSheet('color: #f5f1ff; font-size: 12px;')
        layout.addWidget(self.subtitle)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor(self.colors[0]))
        gradient.setColorAt(1, QColor(self.colors[1]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(self.rect()), 19, 19)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 18), 22))
        painter.drawEllipse(QRectF(self.width() - 105, self.height() - 92, 144, 144))
        painter.setPen(QPen(QColor(255, 255, 255, 12), 1))
        painter.drawEllipse(QRectF(self.width() - 125, self.height() - 112, 185, 185))


def card_layout(widget=None, spacing=12, margins=22):
    frame = widget or QFrame()
    frame.setObjectName('card')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(spacing)
    return frame, layout
