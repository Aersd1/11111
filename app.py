"""文献书架: local desktop research workspace."""
import json
import queue
import sys
import threading
from pathlib import Path
from library_core import Library, SUPPORTED, extract, organize, open_paper
from PySide6.QtCore import Qt, QTimer, QRectF, QEvent
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSplitter, QScrollArea, QTabWidget, QComboBox,
    QStackedWidget, QFileDialog, QMessageBox, QMenu, QDialog, QFormLayout, QPlainTextEdit,
    QProgressBar, QStyledItemDelegate, QStyle, QInputDialog, QCheckBox)
from ui_theme import STYLE, label, button, icon, StatCard, card_layout, setup_application
from ui_settings import SettingsDialog
from ui_catalog import CatalogPage, FoldTree, group_item
from ui_search import SearchPage
from ui_drag import PaperTable, PAPER_MIME
from paper_sections import extract_end
from paper_links import parse_links, decode_links, links_text, open_link
from folder_paths import parts, packed, ancestors, contains, ui_key
from fulltext_agent import analyze, combined_questions, questions_from, QUESTIONS
from model_response import ModelResponseError
from markdown_ui import MarkdownEdit, MarkdownPreview, editor_toolbar, markdown_view, open_typeset


class PaperDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        r = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(r, QColor('#f1ebff' if selected else 'white'))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tile = QRectF(r.x() + 12, r.center().y() - 20, 36, 40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#ede6ff'))
        painter.drawRoundedRect(tile, 8, 8)
        icon('doc', '#8655e8').paint(painter, int(tile.x() + 8), int(tile.y() + 9), 20, 22)
        title, meta = index.data(Qt.ItemDataRole.UserRole) or ('', '')
        left, width = r.x() + 61, max(20, r.width() - 73)
        font = QFont(option.font)
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor('#292a43'))
        painter.drawText(left, r.center().y() - 2, painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, width))
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor('#9499ac'))
        painter.drawText(left, r.center().y() + 19, painter.fontMetrics().elidedText(meta, Qt.TextElideMode.ElideRight, width))
        painter.setPen(QPen(QColor('#f0f1f7'), 1))
        painter.drawLine(r.bottomLeft(), r.bottomRight())
        painter.restore()


class StatusDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        r = option.rect
        painter.fillRect(r, QColor('#f1ebff' if option.state & QStyle.StateFlag.State_Selected else 'white'))
        value = index.data() or ''
        bg, fg = ('#fff0f2', '#be5267') if ('失败' in value or '失联' in value) else ('#fff5e5', '#a37428') if '待' in value else ('#eaf7f2', '#31856a')
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        box = QRectF(r.x() + 9, r.center().y() - 14, min(r.width() - 18, 96), 28)
        painter.drawRoundedRect(box, 8, 8)
        font = QFont(option.font)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor(fg))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, value)
        painter.setPen(QPen(QColor('#f0f1f7'), 1))
        painter.drawLine(r.bottomLeft(), r.bottomRight())
        painter.restore()


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()


class App(QMainWindow):
    def __init__(self, library=None):
        super().__init__()
        self.library = library or Library()
        self.rows, self.busy, self.category, self.only_pending = [], False, None, False
        self.ui_state = self.library.settings().get('ui_state', {})
        self.collapsed_categories = set(self.ui_state.get('collapsed_categories', []))
        self.rebuilding_tree = False
        self.page_number, self.page_size = 0, 50
        self.layout_timer = QTimer(self)
        self.layout_timer.setSingleShot(True)
        self.layout_timer.timeout.connect(self.save_layout)
        self.events = queue.Queue()
        self.setWindowTitle('文献书架 · Research Library')
        self.setWindowIcon(icon('library', '#7849e9'))
        self.resize(1510, 925)
        self.setMinimumSize(1200, 750)
        self.setStyleSheet(STYLE)
        self.build()
        self.setAcceptDrops(True)
        self.install_drop_filters()
        self.drop_feedback_timer = QTimer(self)
        self.drop_feedback_timer.setSingleShot(True)
        self.drop_feedback_timer.timeout.connect(self.clear_drop_hint)
        self.refresh()
        self.outer_splitter.setSizes(self.ui_state.get('outer_sizes', [232, 1150]))
        self.splitter.setSizes(self.ui_state.get('overview_sizes', [650, 340]))
        self.catalog.splitter.setSizes(self.ui_state.get('catalog', {}).get('sizes', [730, 300]))
        for column, width in enumerate(self.ui_state.get('overview_columns', [])[:3]):
            self.table.setColumnWidth(column, width)
        self.search_page.splitter.setSizes(self.ui_state.get('search_sizes', [700, 330]))
        for splitter in (self.outer_splitter, self.splitter, self.catalog.splitter, self.search_page.splitter):
            splitter.handle(1).setToolTip('按住并拖动，调整两侧区域宽度')
        self.set_page(self.ui_state.get('page', 'overview'))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(120)

    def build(self):
        outside = QWidget()
        outer = QVBoxLayout(outside)
        outer.setContentsMargins(13, 13, 13, 13)
        shell = QWidget()
        shell.setObjectName('shell')
        outer.addWidget(shell)
        self.setCentralWidget(outside)
        body = QHBoxLayout(shell)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        rail = QFrame()
        rail.setObjectName('rail')
        rail.setFixedWidth(70)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(12, 24, 12, 20)
        rail_layout.setSpacing(18)
        logo = button('', self.reset_filter, 'primary', 'library')
        logo.setFixedSize(44, 44)
        logo.setToolTip('文献书架')
        rail_layout.addWidget(logo)
        rail_layout.addSpacing(25)
        for glyph, hint, action in [('grid', '全部文献', self.reset_filter), ('clock', '待整理文献', self.pending_filter), ('plus', '添加文献', self.add_files), ('download', '导出全部索引', self.export)]:
            item = button('', action, 'railButton', glyph)
            item.setFixedSize(44, 44)
            item.setToolTip(hint)
            rail_layout.addWidget(item)
        rail_layout.addStretch()
        for glyph, hint, action in [('refresh', '刷新文件状态', self.refresh), ('settings', '设置与模型接口', self.settings_dialog)]:
            item = button('', action, 'railButton', glyph)
            item.setFixedSize(44, 44)
            item.setToolTip(hint)
            rail_layout.addWidget(item)
        body.addWidget(rail)
        self.outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.outer_splitter.setHandleWidth(12)
        self.outer_splitter.setChildrenCollapsible(False)
        self.outer_splitter.splitterMoved.connect(self.schedule_layout_save)
        body.addWidget(self.outer_splitter, 1)
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setMinimumWidth(195)
        self.sidebar = sidebar
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 28, 20, 20)
        side.setSpacing(12)
        side.addWidget(label('YOUR RESEARCH SPACE', 'eyebrow'))
        side.addWidget(label('我的文献库', 'brand'))
        side.addWidget(label('每一份知识，都有归处。', 'muted'))
        side.addSpacing(22)
        views = QHBoxLayout()
        self.overview_button = button('概览', lambda: self.set_page('overview'), 'soft')
        self.catalog_button = button('文献目录', lambda: self.set_page('catalog'), 'soft')
        self.overview_button.setCheckable(True)
        self.catalog_button.setCheckable(True)
        views.addWidget(self.overview_button)
        views.addWidget(self.catalog_button)
        side.addLayout(views)
        self.search_button = button('搜索文献', lambda: self.set_page('search'), 'navButton', 'search')
        self.search_button.setCheckable(True)
        side.addWidget(self.search_button)
        self.all_button = button('全部文献', self.reset_filter, 'navButton', 'library')
        self.pending_button = button('待整理', self.pending_filter, 'navButton', 'clock')
        for item in (self.all_button, self.pending_button):
            item.setCheckable(True)
            side.addWidget(item)
        side.addSpacing(14)
        category_head = QHBoxLayout()
        category_head.addWidget(label('分类目录', 'eyebrow'))
        category_head.addStretch()
        new_category = button('', self.create_category_dialog, 'ghost', 'plus')
        new_category.setToolTip('新建顶层文件夹；子文件夹可在目录页原位添加')
        category_head.addWidget(new_category)
        side.addLayout(category_head)
        self.tree = FoldTree()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.itemClicked.connect(self.category_clicked)
        self.tree.itemCollapsed.connect(lambda item: self.category_folded(item, True))
        self.tree.itemExpanded.connect(lambda item: self.category_folded(item, False))
        self.tree.setAcceptDrops(True)
        self.tree.viewport().setAcceptDrops(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.category_menu)
        side.addWidget(self.tree, 1)
        self.no_categories = label('添加文献后，分类将显示在这里。', 'muted', True)
        side.addWidget(self.no_categories)
        local_card = QFrame()
        local_card.setStyleSheet('QFrame {background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #faf1ff,stop:1 #edf5ff);border-radius:13px;}')
        local = QVBoxLayout(local_card)
        local.setContentsMargins(14, 16, 14, 16)
        local.addWidget(label('本地索引 · 自由阅读'))
        local.addWidget(label('原文件保留在原位置\n随时打开，无需搬动', 'muted'))
        local.addWidget(button('阅读与接口设置', self.settings_dialog, 'ghost', 'settings'))
        side.addWidget(local_card)
        self.outer_splitter.addWidget(sidebar)
        workspace = QWidget()
        content = QVBoxLayout(workspace)
        content.setContentsMargins(25, 27, 25, 17)
        content.setSpacing(19)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(6)
        self.drop_hint = label('欢迎回到你的研究空间 · 支持拖拽文献添加', 'muted')
        heading.addWidget(self.drop_hint)
        heading.addWidget(label('发现、整理，让知识相连', 'heading'))
        header.addLayout(heading, 1)
        settings = button('', self.settings_dialog, 'ghost', 'settings')
        settings.setToolTip('设置与模型接口')
        header.addWidget(settings)
        self.add_button = button('添加文献', self.add_files, 'primary', 'plus')
        self.add_button.setToolTip('点击选择文件，或将一个／多个 PDF、DOCX、TXT、MD 拖到窗口')
        header.addWidget(self.add_button)
        content.addLayout(header)
        self.pages = QStackedWidget()
        content.addWidget(self.pages, 1)
        workspace_layout = content
        self.overview_page = QWidget()
        content = QVBoxLayout(self.overview_page)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(19)
        self.pages.addWidget(self.overview_page)
        cards = QHBoxLayout()
        cards.setSpacing(16)
        self.total_card = StatCard('文献总览', '本地索引 · 原文保留', ('#16b5e8', '#0783e8'), 'library')
        self.organized_card = StatCard('已归类文献', '多层目录 · 清晰有序', ('#b567ef', '#7150e9'), 'folder')
        self.pending_card = StatCard('等待整理', '补充信息 · 继续探索', ('#f875b0', '#fc9973'), 'spark')
        for card in (self.total_card, self.organized_card, self.pending_card):
            cards.addWidget(card, 1)
        content.addLayout(cards)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(14)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.splitterMoved.connect(self.schedule_layout_save)
        table_card, table_layout = card_layout(margins=19, spacing=14)
        table_card.setMinimumWidth(465)
        table_heading = QHBoxLayout()
        self.list_title = label('全部文献', 'sectionTitle')
        self.result_count = label('', 'muted')
        table_heading.addWidget(self.list_title)
        table_heading.addWidget(self.result_count)
        table_heading.addStretch()
        self.sort = QComboBox()
        self.sort.addItems(['最近添加', '题目 A–Z'])
        self.sort.setFixedWidth(123)
        self.sort.currentIndexChanged.connect(self.reset_pagination)
        table_heading.addWidget(self.sort)
        table_layout.addLayout(table_heading)
        self.search = QLineEdit()
        self.search.setObjectName('search')
        self.search.setPlaceholderText('搜索题目、关键词、摘要或分类…')
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon('search'), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self.reset_pagination)
        table_layout.addWidget(self.search)
        self.stack = QStackedWidget()
        self.table = PaperTable(0, 3)
        self.table.setHorizontalHeaderLabels(['文献 / 题目', '所属目录', '状态'])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 330)
        self.table.setColumnWidth(1, 133)
        self.table.setColumnWidth(2, 109)
        self.table.horizontalHeader().sectionResized.connect(self.schedule_layout_save)
        self.table.setItemDelegateForColumn(0, PaperDelegate(self.table))
        self.table.setItemDelegateForColumn(2, StatusDelegate(self.table))
        self.table.itemSelectionChanged.connect(self.show_detail)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_selected())
        self.stack.addWidget(self.table)
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setSpacing(14)
        empty_layout.addStretch()
        book = QLabel()
        book.setPixmap(icon('library', '#a28ade', 58).pixmap(58, 58))
        book.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(book)
        self.empty_title = label('从第一篇文献开始', 'sectionTitle')
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_title)
        self.empty_hint = label('点击添加，或将 PDF、DOCX、TXT、MD 拖到窗口\n建立你的专属文献库', 'muted', True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_hint)
        self.empty_action = button('添加文献', self.empty_clicked, 'soft', 'plus')
        empty_layout.addWidget(self.empty_action, alignment=Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch()
        self.stack.addWidget(empty)
        table_layout.addWidget(self.stack, 1)
        pagination = QHBoxLayout()
        self.page_label = label('', 'muted')
        pagination.addWidget(self.page_label, 1)
        self.previous_button = button('上一页', lambda: self.change_page(-1), 'ghost')
        self.next_button = button('下一页', lambda: self.change_page(1), 'ghost')
        pagination.addWidget(self.previous_button)
        pagination.addWidget(self.next_button)
        table_layout.addLayout(pagination)
        operations = QHBoxLayout()
        operations.setSpacing(7)
        self.open_button = button('打开', self.open_selected, 'soft', 'open')
        self.organize_button = button('整理', self.process_selected, glyph='spark')
        self.edit_button = button('编辑', self.edit_dialog, glyph='edit')
        self.more_button = button('', self.more_menu, 'ghost', 'more')
        self.more_button.setToolTip('打开方式、重新关联、移除索引与导出')
        for item in (self.open_button, self.organize_button, self.edit_button):
            operations.addWidget(item)
        operations.addStretch()
        operations.addWidget(self.more_button)
        table_layout.addLayout(operations)
        self.splitter.addWidget(table_card)
        detail_card, detail_layout = card_layout(margins=21, spacing=14)
        detail_card.setMinimumWidth(290)
        detail_header = QHBoxLayout()
        detail_header.addWidget(label('文献详情', 'sectionTitle'))
        detail_header.addStretch()
        self.detail_type = label('预览', 'badge')
        detail_header.addWidget(self.detail_type)
        detail_layout.addLayout(detail_header)
        self.detail_title = label('选中一篇文献', 'sectionTitle', True)
        self.detail_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.detail_title)
        self.detail_category = label('在这里快速了解研究内容', 'muted', True)
        detail_layout.addWidget(self.detail_category)
        self.detail_tabs = QTabWidget()
        self.detail_bodies = []
        for title in ('总结', '摘要', '文件'):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            page = QWidget()
            page.setObjectName('detailPage')
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 17, 9, 8)
            layout.setSpacing(13)
            scroll.setWidget(page)
            self.detail_tabs.addTab(scroll, title)
            self.detail_bodies.append(layout)
        detail_layout.addWidget(self.detail_tabs, 1)
        self.detail_open = button('打开原文', self.open_selected, 'primary', 'open')
        detail_layout.addWidget(self.detail_open)
        self.splitter.addWidget(detail_card)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([650, 340])
        content.addWidget(self.splitter, 1)
        footer = QHBoxLayout()
        self.status = label('就绪 · 所有文献通过原路径索引', 'muted')
        footer.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(100)
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        footer.addWidget(self.progress)
        self.mode_label = label('', 'muted')
        footer.addWidget(self.mode_label)
        workspace_layout.addLayout(footer)
        self.catalog = CatalogPage(self, self.ui_state.get('catalog', {}))
        self.catalog.layout_changed.connect(self.schedule_layout_save)
        self.pages.addWidget(self.catalog)
        self.search_page = SearchPage(self)
        self.search_page.splitter.splitterMoved.connect(self.schedule_layout_save)
        self.pages.addWidget(self.search_page)
        self.outer_splitter.addWidget(workspace)
        self.outer_splitter.setStretchFactor(0, 0)
        self.outer_splitter.setStretchFactor(1, 1)

    def set_page(self, name):
        self.pages.setCurrentWidget({'catalog': self.catalog, 'search': self.search_page}.get(name, self.overview_page))
        self.overview_button.setChecked(name not in ('catalog', 'search'))
        self.catalog_button.setChecked(name == 'catalog')
        self.search_button.setChecked(name == 'search')
        self.schedule_layout_save()

    def schedule_layout_save(self, *args):
        self.layout_timer.start(700)

    def save_layout(self):
        self.layout_timer.stop()
        value = {'collapsed_categories': sorted(self.collapsed_categories),
                 'outer_sizes': self.outer_splitter.sizes(), 'overview_sizes': self.splitter.sizes(),
                 'overview_columns': [self.table.columnWidth(i) for i in range(3)],
                 'catalog': self.catalog.state(),
                 'search_sizes': self.search_page.splitter.sizes(),
                 'page': 'search' if self.pages.currentWidget() == self.search_page else 'catalog' if self.pages.currentWidget() == self.catalog else 'overview'}
        settings = self.library.settings()
        self.library.save_settings({**settings, 'ui_state': value})

    def category_folded(self, item, collapsed):
        if self.rebuilding_tree:
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key:
            name = '/'.join(parts(key))
            if collapsed:
                self.collapsed_categories.add(name)
            else:
                self.collapsed_categories.discard(name)
            self.schedule_layout_save()

    def reset_pagination(self, *args):
        self.page_number = 0
        self.refresh_list()

    def change_page(self, delta):
        self.page_number += delta
        self.refresh_list()

    @staticmethod
    def needs_attention(paper):
        return paper['major'] == '待分类' or '待' in paper['status'] or '失败' in paper['status']

    def selected_ids(self):
        if hasattr(self, 'search_page') and self.pages.currentWidget() == self.search_page:
            return self.search_page.selected_ids()
        if hasattr(self, 'catalog') and self.pages.currentWidget() == self.catalog:
            return self.catalog.selected_ids()
        return self.table_selected_ids()

    def table_selected_ids(self):
        return [int(self.table.item(i.row(), 0).data(Qt.ItemDataRole.UserRole + 1)) for i in self.table.selectionModel().selectedRows()]

    def selected(self):
        ids = self.selected_ids()
        return self.library.get(ids[0]) if ids else None

    def refresh(self):
        from disk_catalog import sync_catalog
        try:
            sync_catalog(self.library)
        except (OSError, ValueError) as exc:
            self.status.setText('磁盘目录同步失败：' + str(exc))
        self.rows = self.library.all()
        self.rebuilding_tree = True
        self.tree.clear()
        groups = {}
        for paper in self.rows:
            groups.setdefault(paper['major'], {}).setdefault(paper['minor'], 0)
            groups[paper['major']][paper['minor']] += 1
        for major, minor in self.library.categories():
            groups.setdefault(major, {})
            if minor:
                groups[major].setdefault(minor, 0)
        folder_counts, folder_items = {}, {}
        for paper in self.rows:
            for key in ancestors((paper['major'], paper['minor'])):
                folder_counts[key] = folder_counts.get(key, 0) + 1
        for key in self.library.directory_keys():
            path = parts(key)
            item = group_item(f'{path[-1]}  {folder_counts.get(key, 0)}', ui_key(key))
            item.setToolTip(0, ' / '.join(path))
            if len(path) == 1:
                self.tree.addTopLevelItem(item)
            else:
                folder_items[packed(path[:-1])].addChild(item)
            folder_items[key] = item
            item.setExpanded('/'.join(path) not in self.collapsed_categories)
            if self.category == ui_key(key):
                self.tree.setCurrentItem(item)
        self.rebuilding_tree = False
        self.no_categories.setVisible(not groups)
        total = len(self.rows)
        pending = sum(self.needs_attention(p) for p in self.rows)
        self.total_card.number.setText(str(total))
        self.organized_card.number.setText(str(sum(p['major'] != '待分类' for p in self.rows)))
        self.organized_card.subtitle.setText(f'{len(folder_items)} 个文件夹 · 多层目录')
        self.pending_card.number.setText(str(pending))
        self.all_button.setText(f'全部文献    {total}')
        self.pending_button.setText(f'待整理       {pending}')
        self.mode_label.setText('整理方式：' + self.library.settings()['mode'])
        if hasattr(self, 'search_page'):
            self.search_page.set_rows(self.rows)
        self.refresh_list()

    def reset_filter(self):
        self.category, self.only_pending = None, False
        self.page_number = 0
        if hasattr(self, 'catalog'):
            self.catalog.search.clear()
        self.tree.clearSelection()
        self.search.clear()
        self.refresh_list()

    def pending_filter(self):
        self.category, self.only_pending = None, True
        self.page_number = 0
        self.tree.clearSelection()
        self.refresh_list()

    def category_clicked(self, item, column):
        self.category = tuple(item.data(0, Qt.ItemDataRole.UserRole))
        self.only_pending = False
        self.page_number = 0
        self.refresh_list()

    def refresh_list(self, *args):
        if not hasattr(self, 'table'):
            return
        selected = set(self.table_selected_ids())
        query = self.search.text().strip().casefold()
        visible, directory_rows = [], []
        for paper in self.rows:
            if self.only_pending and not self.needs_attention(paper):
                continue
            if self.category and not contains(self.category, (paper['major'], paper['minor'])):
                continue
            directory_rows.append(paper)
            if query and query not in ' '.join(str(paper[k]) for k in ('title', 'abstract', 'keywords', 'summary', 'major', 'minor', 'note', 'path', 'conclusion', 'limitations', 'description')).casefold():
                continue
            visible.append(paper)
        if self.sort.currentIndex() == 1:
            visible.sort(key=lambda p: p['title'].casefold())
        if hasattr(self, 'catalog'):
            self.catalog.set_rows(directory_rows)
        total = len(visible)
        pages = max(1, (total + self.page_size - 1) // self.page_size)
        self.page_number = max(0, min(self.page_number, pages - 1))
        start = self.page_number * self.page_size
        current_page = visible[start:start + self.page_size]
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for paper in current_page:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setRowHeight(row, 79)
            title = QTableWidgetItem(paper['title'])
            title.setData(Qt.ItemDataRole.UserRole, (paper['title'], f'{Path(paper["path"]).suffix[1:].upper()}  ·  {paper["added"][:10]}'))
            title.setData(Qt.ItemDataRole.UserRole + 1, paper['id'])
            title.setToolTip(paper['title'] + '\n' + paper['path'])
            self.table.setItem(row, 0, title)
            category = QTableWidgetItem(paper['major'] + '\n' + paper['minor'])
            category.setToolTip(paper['major'] + ' / ' + paper['minor'])
            category.setForeground(QColor('#7a7392'))
            self.table.setItem(row, 1, category)
            state = paper['status'] if Path(paper['path']).is_file() else '文件失联'
            self.table.setItem(row, 2, QTableWidgetItem(state))
            if paper['id'] in selected:
                for col in range(3):
                    self.table.item(row, col).setSelected(True)
        if visible and not self.table.selectionModel().selectedRows():
            self.table.selectRow(0)
        self.table.blockSignals(False)
        self.stack.setCurrentIndex(0 if visible else 1)
        self.empty_title.setText('没有匹配的文献' if self.rows else '从第一篇文献开始')
        self.empty_hint.setText('试试其他关键词，或清除筛选条件。' if self.rows else '点击添加，或将 PDF、DOCX、TXT、MD 拖到窗口\n建立你的专属文献库')
        self.empty_action.setText('清除筛选' if self.rows else '添加文献')
        self.result_count.setText(f'{len(visible)} 篇')
        self.page_label.setText(f'{start + 1 if total else 0}–{min(start + self.page_size, total)} / {total} 篇 · {self.page_number + 1}/{pages} 页')
        self.previous_button.setEnabled(self.page_number > 0)
        self.next_button.setEnabled(self.page_number < pages - 1)
        self.list_title.setText(' / '.join(self.category) if self.category else '待整理' if self.only_pending else '全部文献')
        self.list_title.setMaximumWidth(230)
        self.list_title.setToolTip(self.list_title.text())
        self.all_button.setChecked(not self.category and not self.only_pending)
        self.pending_button.setChecked(self.only_pending)
        self.show_detail()

    def empty_clicked(self):
        self.reset_filter() if self.rows else self.add_files()

    def show_detail(self):
        ids = self.table_selected_ids()
        paper = self.library.get(ids[0]) if ids else None
        if hasattr(self, 'catalog'):
            self.catalog.show_detail()
        if hasattr(self, 'search_page'):
            self.search_page.show_detail()
        for control in (self.open_button, self.detail_open, self.organize_button, self.edit_button):
            control.setEnabled(bool(paper) and (not self.busy or control in (self.open_button, self.detail_open)))
        for layout in self.detail_bodies:
            clear_layout(layout)
        if not paper:
            self.detail_title.setText('选中一篇文献')
            self.detail_category.setText('在这里快速了解研究内容')
            self.detail_type.setText('预览')
            for layout, text in zip(self.detail_bodies, ['总结与研究要点会显示在这里。', '查看摘要、引言前两段、结论和局限。', '原文件位置、整理依据和处理状态。']):
                layout.addWidget(label(text, 'muted', True))
                layout.addStretch()
            return
        self.detail_title.setText(paper['title'])
        self.detail_title.setMaximumHeight(115)
        self.detail_title.setToolTip(paper['title'])
        self.detail_category.setText(paper['major'] + ' / ' + paper['minor'])
        self.detail_type.setText(Path(paper['path']).suffix[1:].upper())
        def section(layout, title, text):
            layout.addWidget(label(title, 'eyebrow'))
            if title in ('研究总结', '个人描述'):
                layout.addWidget(markdown_view(text))
                return
            value = label(text, wrap=True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setStyleSheet('font-size:14px; color:#646781;')
            layout.addWidget(value)
        summary, abstract, file = self.detail_bodies
        section(summary, '研究总结', paper['summary'] or '尚未生成总结。点击“整理”开始，或通过“编辑”补充摘要。')
        section(summary, '整理依据', paper['basis'] or '尚未整理')
        summary.addWidget(button('全文 Agent 分析 / 再次分析', self.fulltext_dialog, 'soft'))
        summary.addWidget(button('总结历史版本', self.history_dialog, 'soft'))
        summary.addWidget(button('总结排版预览（含公式）', lambda: self.open_markdown_preview(paper['summary']), 'soft'))
        summary.addWidget(label('可选全文阅读；普通整理仍使用摘要等信息。请结合原文核对生成内容。', 'muted', True))
        section(abstract, '摘要', paper['abstract'] or '尚未识别，可手动补充。')
        section(abstract, '关键词', paper['keywords'] or '尚未识别')
        section(abstract, '引言前两段', paper['introduction'] or '尚未识别')
        section(abstract, '结论', paper.get('conclusion') or '尚未识别，可重新读取结论或手动补充。')
        section(abstract, '局限段', paper.get('limitations') or '尚未识别独立局限段，请结合结论核对。')
        section(abstract, '结论提取范围', paper.get('end_note') or '旧文献尚未读取结论')
        section(file, '个人描述', paper.get('description') or '尚未填写，可点击编辑补充。')
        for link in decode_links(paper.get('links')):
            link_button = button(link['label'], lambda url=link['url']: self.open_reference(url), 'soft', 'open')
            link_button.setToolTip(link['url'])
            file.addWidget(link_button)
        section(file, '原文件位置', paper['path'])
        section(file, '处理状态', paper['status'])
        if paper['note']:
            section(file, '处理说明', paper['note'])
        section(file, '添加时间', paper['added'] + ' UTC')
        file.addWidget(button('重新关联文件', self.relink, 'soft', 'folder'))
        for layout in self.detail_bodies:
            layout.addStretch()
        self.install_drop_filters()

    def idle_required(self):
        if self.busy:
            QMessageBox.information(self, '正在整理', '请等待当前批次完成后再修改文献或设置。')
            return False
        return True

    def create_category_dialog(self, parent_major=None):
        if not self.idle_required():
            return
        self.catalog.begin_new_folder(parent_major or None)

    def category_menu(self, point):
        item = self.tree.itemAt(point)
        key = tuple(item.data(0, Qt.ItemDataRole.UserRole)) if item else None
        menu = QMenu(self)
        menu.addAction('新建顶层文件夹', lambda: self.create_category_dialog())
        if key:
            menu.addAction('在此新建子文件夹', lambda: self.create_category_dialog(key[0] if len(key) == 1 else key))
            menu.addAction('重命名目录', lambda: self.rename_category_dialog(key))
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def rename_category_dialog(self, key):
        if not self.idle_required():
            return
        self.catalog.begin_rename_folder(tuple(key))

    def open_reference(self, target):
        try:
            open_link(target)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, '无法打开关联链接', str(exc))

    def delete_category_dialog(self, key):
        if not self.idle_required():
            return
        answer = QMessageBox.question(self, '删除分类文件夹',
            '删除“' + ' / '.join(key) + '”？\n其中的文献快捷方式将回到待分类，原文件和个人描述会保留。\n文件夹内自行存放的其他文件不会删除。')
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.library.delete_category(key[0], key[1] if len(key) > 1 else '')
        except ValueError as exc:
            QMessageBox.warning(self, '无法删除', str(exc))
            return
        self.category = None
        self.refresh()

    def fill_missing_end(self):
        if self.idle_required():
            ids = [p['id'] for p in self.library.all() if not p.get('end_checked')]
            if ids:
                self.process(ids, end_only=True)
            else:
                self.status.setText('所有文献均已尝试读取结论。可在“…”中重新读取所选文献。')

    def read_selected_end(self):
        if self.idle_required() and self.selected_ids():
            self.process(self.selected_ids(), end_only=True)

    def drop_category_target(self, watched, point):
        for tree in (self.tree, self.catalog.tree):
            if not tree.isVisible():
                continue
            # The source and target can be siblings, not ancestors.
            local = tree.viewport().mapFromGlobal(watched.mapToGlobal(point))
            if not tree.viewport().rect().contains(local):
                continue
            item = tree.itemAt(local)
            if not item:
                return None
            key = tuple(item.data(0, Qt.ItemDataRole.UserRole) or ())
            if tree == self.tree:
                return (key[0], key[1] if len(key) > 1 else '未细分') if key else None
            if key and key[0] == 'major':
                return key[1], '未细分'
            if key and key[0] == 'minor':
                return key[1], key[2]
        return None

    def category_drop(self, watched, event):
        source = event.source()
        allowed = (self.table, self.catalog.tree, self.search_page.tree)
        if self.busy or source not in allowed:
            event.ignore()
            return True
        target = self.drop_category_target(watched, event.position().toPoint())
        try:
            ids = json.loads(bytes(event.mimeData().data(PAPER_MIME)).decode('utf-8'))
            if not isinstance(ids, list) or not ids or len(ids) > 10000 or any(type(i) is not int for i in ids):
                raise ValueError()
        except (ValueError, UnicodeError):
            event.ignore()
            return True
        if not target:
            self.show_drop_hint('请将文献拖到左侧或目录页的目标文件夹上')
            event.ignore()
            return True
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.show_drop_hint(f'归入 {target[0]} / {target[1]} · 保留原文件与个人描述')
        if event.type() == QEvent.Type.Drop:
            # Wait until Qt completes source drag handling before rebuilding rows.
            QTimer.singleShot(0, lambda: self.assign_papers(ids, target))
        return True

    def assign_papers(self, ids, target):
        if self.busy:
            return
        try:
            self.library.assign_category(ids, *target)
        except ValueError as exc:
            QMessageBox.warning(self, '无法归类', str(exc))
            return
        self.refresh()
        self.show_drop_hint(f'已将 {len(set(ids))} 篇文献归入 {target[0]} / {target[1]}，分类已锁定')
        self.drop_feedback_timer.start(5500)

    @staticmethod
    def dropped_files(mime):
        paths, ignored = [], 0
        for url in mime.urls():
            if not url.isLocalFile():
                ignored += 1
                continue
            path = Path(url.toLocalFile())
            try:
                supported = path.suffix.lower() in SUPPORTED and path.is_file()
            except OSError:
                supported = False
            if supported:
                paths.append(str(path))
            else:
                ignored += 1
        return paths, ignored

    def clear_drop_hint(self):
        self.drop_hint.setText('欢迎回到你的研究空间 · 支持拖拽文献添加')
        self.drop_hint.setStyleSheet('')

    def show_drop_hint(self, text):
        self.drop_hint.setText(text)
        self.drop_hint.setStyleSheet('color:#7546dc; font-weight:600;')

    def eventFilter(self, watched, event):
        # Text fields and scroll viewports can otherwise consume file drops as
        # pasted URLs. Intercept only file URL drags inside this main window;
        # configuration and editing dialogs retain their own normal behavior.
        kinds = (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop, QEvent.Type.DragLeave)
        if event.type() not in kinds or not isinstance(watched, QWidget) or watched.window() != self:
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.DragLeave:
            self.clear_drop_hint()
            event.accept()
            return True
        if event.mimeData().hasFormat(PAPER_MIME):
            self.drop_feedback_timer.stop()
            return self.category_drop(watched, event)
        if not event.mimeData().hasUrls():
            return super().eventFilter(watched, event)
        self.drop_feedback_timer.stop()
        paths, ignored = self.dropped_files(event.mimeData())
        if self.busy:
            self.show_drop_hint('当前批次正在整理，完成后可继续拖入文件')
            event.ignore()
            self.drop_feedback_timer.start(4500)
            return True
        if not paths or not event.possibleActions() & Qt.DropAction.CopyAction:
            self.show_drop_hint('请拖入本地 PDF、DOCX、TXT 或 MD 文件；不支持文件夹和网页链接')
            event.ignore()
            self.drop_feedback_timer.start(4500)
            return True
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if event.type() == QEvent.Type.Drop:
            self.import_paths(paths, self.drop_category_target(watched, event.position().toPoint()))
            suffix = f'，已忽略 {ignored} 个不支持的项目' if ignored else ''
            self.show_drop_hint(f'已接收 {len(paths)} 个文件{suffix} · 原文件保留在原位置')
            self.drop_feedback_timer.start(5500)
        else:
            suffix = f'（另有 {ignored} 项将忽略）' if ignored else ''
            self.show_drop_hint(f'松开鼠标，添加 {len(paths)} 个文献索引{suffix}')
        return True

    def install_drop_filters(self):
        # A Python application-wide filter sees Chromium's private QObjects,
        # including objects whose metaobject is being destroyed. Filter only
        # our widget tree and never descend into the embedded browser.
        def visit(widget):
            widget.installEventFilter(self)
            if isinstance(widget, MarkdownPreview):
                return
            for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
                if not child.isWindow():
                    visit(child)
        visit(self)

    def add_files(self):
        if self.idle_required():
            paths, _ = QFileDialog.getOpenFileNames(self, '添加文献索引 · 原文件不移动', '', '文献 (*.pdf *.docx *.txt *.md)')
            if paths:
                self.import_paths(paths)

    def import_paths(self, paths, target_category=None):
        if self.busy:
            self.status.setText('当前批次尚未完成，请完成后继续添加文献。')
            return
        ids, skipped, errors, all_ids = [], 0, [], []
        for path in paths:
            try:
                paper_id, created = self.library.add(path)
                all_ids.append(paper_id)
                if created:
                    ids.append(paper_id)
                else:
                    skipped += 1
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
        if target_category and all_ids:
            self.library.assign_category(all_ids, *target_category)
        self.reset_filter()
        self.refresh()
        if errors:
            QMessageBox.warning(self, '部分文件未添加', '\n'.join(errors[:5]))
        if ids:
            self.process(ids, extract_first=True)
        else:
            self.status.setText(f'没有新增文献 · {skipped} 个文件已有索引')

    def process_selected(self):
        if self.idle_required() and self.selected_ids():
            self.process(self.selected_ids())

    def open_markdown_preview(self, text):
        try:
            open_typeset(text)
        except Exception:
            QMessageBox.warning(self, '无法打开排版预览', '请检查默认浏览器和安装包中的离线公式组件。')

    def fulltext_dialog(self):
        ids = self.selected_ids()
        if not ids or not self.idle_required():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('全文 Agent 分析')
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(f'分析所选 {len(ids)} 篇文献的完整可提取正文', 'heading'))
        layout.addWidget(label('每篇一次提交完整可提取正文，只回答预设问题和补充问题，不再逐段生成笔记或回读。原总结会保存为历史版本。若全文超出模型上下文，需更换支持长文的模型。', 'muted', True))
        layout.addWidget(label('补充问题 · 每行一个（最多 30 行）' if len(ids) == 1 else '本批次补充问题 · 追加到各篇已保存的问题', 'sectionTitle'))
        extra = QPlainTextEdit()
        extra.setPlaceholderText('例如：这篇论文的方法如何用于我的数据？\n实验中最重要的对照是什么？')
        if len(ids) == 1:
            extra.setPlainText(self.library.get(ids[0]).get('analysis_extra_questions') or '')
        layout.addWidget(extra)
        reuse = QCheckBox('问题、正文及配置未变时复用已完成的回答（不发请求）')
        reuse.setChecked(True)
        layout.addWidget(reuse)
        layout.addWidget(label('预设问题在“设置 → 全文分析”修改；每篇补充问题也可在“编辑文献 → 补充问题”修改。思考模式沿用设置。', 'muted', True))
        def start():
            try:
                preset = self.library.settings().get('analysis_questions', '\n'.join(QUESTIONS))
                for ident in ids:
                    saved = self.library.get(ident).get('analysis_extra_questions') or ''
                    combined_questions(preset, extra.toPlainText() if len(ids) == 1 else saved + '\n' + extra.toPlainText())
            except ValueError as exc:
                QMessageBox.warning(dialog, '补充问题格式错误', str(exc))
                return
            dialog.accept()
        layout.addWidget(button('开始全文分析', start, 'primary'))
        layout.addWidget(button('取消', dialog.reject))
        dialog.resize(640, 510)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if len(ids) == 1:
                self.library.update(ids[0], analysis_extra_questions=extra.toPlainText().strip())
            self.process_fulltext(ids, reuse.isChecked(), extra.toPlainText().strip() if len(ids) > 1 else '')

    def process_fulltext(self, ids, reuse=True, extra_questions=''):
        self.busy = True
        self.progress.setVisible(True)
        self.add_button.setEnabled(False)
        settings = self.library.settings()
        self.show_detail()
        def work():
            failures = 0
            for number, paper_id in enumerate(ids, 1):
                paper = self.library.get(paper_id)
                try:
                    summary, info = analyze(paper['path'], settings, self.library.path.parent / 'analysis-cache', reuse,
                        progress=lambda text: self.events.put(('status', f'{number}/{len(ids)} · {text}')),
                        supplementary_questions=(paper.get('analysis_extra_questions') or '') + '\n' + extra_questions)
                    self.library.update(paper_id, summary=summary, analysis_info=json.dumps(info, ensure_ascii=False),
                        basis=f'全文问答 · {len(info["questions"])} 个问题 · {info["calls"]} 次调用' + (' · 复用完整回答' if info.get('cached_result') else ''),
                        status='全文已分析', note='')
                except Exception as exc:
                    failures += 1
                    reason = str(exc) if isinstance(exc, (ValueError, ModelResponseError)) else '全文分析失败，请检查原文件和模型接口；原总结已保留。'
                    self.library.update(paper_id, status='全文分析失败', note=reason[:500])
                self.events.put(('refresh', None))
            self.events.put(('done', f'全文分析完成 · {len(ids)} 篇，{failures} 篇失败'))
        threading.Thread(target=work, daemon=True).start()

    def history_dialog(self):
        paper = self.selected()
        if not paper or not self.idle_required():
            return
        versions = self.library.summary_history(paper['id'])
        dialog = QDialog(self)
        dialog.setWindowTitle('总结历史版本')
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.resize(800, 650)
        layout = QVBoxLayout(dialog)
        selector = QComboBox()
        for version in versions:
            selector.addItem(f'{version["created"]} UTC · {version["basis"] or "总结"}', version['id'])
        layout.addWidget(selector)
        preview = MarkdownPreview('暂无历史版本。替换或编辑总结时会自动保存旧版本。')
        layout.addWidget(preview, 1)
        from markdown_ui import markdown_html
        def show_version():
            if selector.currentIndex() >= 0:
                preview.setHtml(markdown_html(versions[selector.currentIndex()]['summary']))
        selector.currentIndexChanged.connect(show_version)
        show_version()
        def restore():
            self.library.restore_summary(paper['id'], selector.currentData())
            dialog.accept()
            self.refresh()
        control = button('恢复此版本（当前总结也会备份）', restore, 'primary')
        control.setEnabled(bool(versions))
        layout.addWidget(control)
        layout.addWidget(button('关闭', dialog.reject))
        dialog.exec()

    def process(self, ids, extract_first=False, end_only=False):
        self.busy = True
        self.progress.setVisible(True)
        self.add_button.setEnabled(False)
        self.show_detail()
        settings = self.library.settings()
        def process_one(number, paper_id):
            failures = 0
            paper = self.library.get(paper_id)
            self.events.put(('status', f'正在整理 {number}/{len(ids)} · {paper["title"][:24]}'))
            try:
                if extract_first:
                    fields = extract(paper['path'])
                    self.library.update(paper_id, **fields)
                    paper.update(fields)
                if end_only or not paper.get('end_checked'):
                    try:
                        fields = extract_end(paper['path'])
                    except Exception:
                        fields = {'end_checked': 1, 'end_note': '结论读取失败，请检查文件或手动补充。'}
                        if end_only:
                            failures += 1
                    if not end_only:
                        # Never overwrite conclusions already corrected by the user.
                        for key in ('conclusion', 'limitations'):
                            if paper.get(key):
                                fields.pop(key, None)
                    self.library.update(paper_id, **fields)
                    paper.update(fields)
                if end_only:
                    self.events.put(('refresh', None))
                    return failures
                if not any(paper.get(k) for k in ('abstract', 'keywords', 'introduction', 'conclusion', 'limitations')):
                    self.library.update(paper_id, status='待补充摘要', note='请编辑补充摘要或关键词后重新整理。', summary='信息不足：暂未生成总结。')
                else:
                    categories = [(m, n) for m, n in self.library.categories() if n and m != '待分类']
                    result = organize(paper, settings, categories)
                    self.library.update(paper_id, **result, note='')
            except Exception as exc:
                failures += 1
                reason = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else '文件读取或处理失败，请检查格式、加密状态与依赖。'
                self.library.update(paper_id, status='处理失败', note=reason[:300])
            self.events.put(('refresh', None))
            return failures

        def work():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            failures = completed = 0
            workers = 2 if settings['mode'] == '大模型' and not end_only else 1
            with ThreadPoolExecutor(max_workers=workers) as pool:
                pending = [pool.submit(process_one, n, ident) for n, ident in enumerate(ids, 1)]
                for future in as_completed(pending):
                    try:
                        failures += future.result()
                    except Exception:
                        failures += 1
                    completed += 1
                    self.events.put(('status', f'整理进度 {completed}/{len(ids)} · {failures} 篇失败'))
            self.events.put(('done', f'整理完成 · {len(ids)} 篇，{failures} 篇失败' if failures else f'整理完成 · 已处理 {len(ids)} 篇文献'))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'refresh':
                self.refresh()
            elif kind == 'done':
                self.busy = False
                self.progress.setVisible(False)
                self.add_button.setEnabled(True)
                self.status.setText(value)
                self.show_detail()
            else:
                self.status.setText(value)

    def open_selected(self, override=None):
        paper = self.selected()
        if paper:
            try:
                open_paper(paper['path'], self.library.settings(), override)
            except Exception as exc:
                QMessageBox.warning(self, '无法打开', str(exc))

    def more_menu(self):
        menu = QMenu(self)
        opening = menu.addMenu('本次打开方式')
        for mode in ('系统默认', '浏览器', '指定程序'):
            opening.addAction(mode, lambda m=mode: self.open_selected(m))
        opening.setEnabled(bool(self.selected()))
        menu.addAction('重新关联原文件', self.relink).setEnabled(bool(self.selected()))
        menu.addAction('读取所选结论（不调用模型）', self.read_selected_end).setEnabled(bool(self.selected()))
        menu.addAction('全文 Agent 分析 / 再次分析', self.fulltext_dialog).setEnabled(bool(self.selected()))
        menu.addAction('总结历史版本', self.history_dialog).setEnabled(bool(self.selected()))
        menu.addSeparator()
        menu.addAction('导出全部索引 JSON', self.export)
        menu.addAction('刷新文件状态', self.refresh)
        menu.addSeparator()
        menu.addAction('移除所选索引（保留原文件）', self.remove).setEnabled(bool(self.selected()))
        anchor = self.search_page.more_button if self.pages.currentWidget() == self.search_page else self.catalog.more_button if self.pages.currentWidget() == self.catalog else self.more_button
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def relink(self):
        paper = self.selected()
        if not paper or not self.idle_required():
            return
        path, _ = QFileDialog.getOpenFileName(self, '重新关联文献', '', '文献 (*.pdf *.docx *.txt *.md)')
        if path:
            resolved = str(Path(path).resolve())
            if any(p['id'] != paper['id'] and p['path'].casefold() == resolved.casefold() for p in self.rows):
                QMessageBox.warning(self, '已存在', '所选文件已有索引。')
                return
            self.library.update(paper['id'], path=resolved)
            self.refresh()

    def remove(self):
        ids = self.selected_ids()
        if not ids or not self.idle_required():
            return
        if QMessageBox.question(self, '移除索引', f'移除 {len(ids)} 条索引？原文件仍会保留。') == QMessageBox.StandardButton.Yes:
            for paper_id in ids:
                self.library.remove(paper_id)
            self.refresh()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, '导出全部索引', '文献索引.json', 'JSON (*.json)')
        if path:
            try:
                Path(path).write_text(json.dumps(self.library.all(), ensure_ascii=False, indent=2), encoding='utf-8')
                self.status.setText('索引已导出 · 不包含接口密钥')
            except OSError:
                QMessageBox.warning(self, '导出失败', '无法写入所选位置。')

    def edit_dialog(self):
        paper = self.selected()
        if not paper or not self.idle_required():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('编辑文献')
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.resize(1220, 850)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(25, 25, 25, 22)
        layout.addWidget(label('校正文献信息', 'heading'))
        layout.addWidget(label('个人描述和链接始终保留。重新整理会生成新总结；已锁定的手动分类不会被覆盖。', 'muted', True))
        form = QFormLayout()
        form.setVerticalSpacing(12)
        fields = {}
        for key, caption in [('title', '题目'), ('major', '顶层目录'), ('minor', '子目录路径（用 / 分层）'), ('keywords', '关键词')]:
            if key in ('major', 'minor'):
                entry = QComboBox()
                entry.setEditable(True)
                entry.addItems(sorted({p[key] for p in self.rows}))
                entry.setCurrentText(paper[key])
            else:
                entry = QLineEdit(paper[key])
            fields[key] = entry
            form.addRow(caption, entry)
        layout.addLayout(form)
        tabs = QTabWidget()
        texts = {}
        for key, caption in [('abstract', '摘要'), ('introduction', '引言'), ('conclusion', '结论'), ('limitations', '局限'), ('summary', '总结'), ('analysis_extra_questions', '补充问题'), ('description', '个人描述')]:
            entry = MarkdownEdit(paper[key])
            texts[key] = entry
            tabs.addTab(entry, caption)
        link_edit = QPlainTextEdit(links_text(paper.get('links')))
        link_edit.setPlaceholderText('每行一个：名称 | https://地址\n或：附件名称 | D:\\文献\\附件.pdf')
        tabs.addTab(link_edit, '关联链接')
        layout.addWidget(editor_toolbar(tabs.currentWidget, lambda: self.open_markdown_preview(tabs.currentWidget().toPlainText())))
        layout.addWidget(label('左侧编辑，右侧实时排版。公式使用 $...$ 或 $$...$$；Ctrl+B 加粗、Ctrl+I 斜体、Ctrl+K 插入链接。拖动中间分隔条调整宽度。', 'muted', True))
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(label('编辑内容 · Markdown / LaTeX', 'eyebrow'))
        left_layout.addWidget(tabs)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(label('实时预览 · 公式与链接', 'eyebrow'))
        preview = MarkdownPreview(tabs.currentWidget().toPlainText())
        right_layout.addWidget(preview)
        split.addWidget(left)
        split.addWidget(right)
        split.setChildrenCollapsible(False)
        split.setSizes([580, 580])
        layout.addWidget(split, 1)
        def refresh_preview():
            preview.setMarkdown(tabs.currentWidget().toPlainText())
        tabs.currentChanged.connect(refresh_preview)
        for entry in [*texts.values(), link_edit]:
            entry.textChanged.connect(refresh_preview)
        lock = QCheckBox('保留手动分类（再次整理时只更新总结，不覆盖分类）')
        lock.setChecked(bool(paper.get('category_locked')))
        layout.addWidget(lock)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button('取消', dialog.reject))
        def save():
            value = {k: (v.currentText() if isinstance(v, QComboBox) else v.text()).strip() for k, v in fields.items()}
            if any(not value[k] for k in ('title', 'major', 'minor')):
                QMessageBox.warning(dialog, '内容不完整', '请填写题目、大类和小类。')
                return
            value.update({k: v.toPlainText().strip() for k, v in texts.items()})
            try:
                if value['analysis_extra_questions']:
                    questions_from(value['analysis_extra_questions'])
                value['links'] = parse_links(link_edit.toPlainText())
            except ValueError as exc:
                QMessageBox.warning(dialog, '内容格式错误', str(exc))
                return
            value['category_locked'] = int(lock.isChecked() or value['major'] != paper['major'] or value['minor'] != paper['minor'])
            if value['conclusion'] != paper.get('conclusion') or value['limitations'] != paper.get('limitations'):
                value['end_checked'] = 1
                value['end_note'] = '结论／局限由用户手动编辑'
            if value['summary'] != paper['summary']:
                value['basis'] = '手动编辑'
                value['analysis_info'] = '{}'
            self.library.update(paper['id'], **value, status='手动已编辑')
            dialog.accept()
            self.refresh()
        row.addWidget(button('保存修改', save, 'primary', 'check'))
        layout.addLayout(row)
        dialog.exec()

    def settings_dialog(self):
        if self.idle_required():
            self.save_layout()
            dialog = SettingsDialog(self.library, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.refresh()
                self.status.setText('设置已保存 · 新发起的整理将使用当前接口配置')

    def closeEvent(self, event):
        if self.busy and QMessageBox.question(self, '仍在整理', '当前批次尚未完成，已处理结果已保存。确定退出？') != QMessageBox.StandardButton.Yes:
            event.ignore()
        else:
            self.save_layout()
            event.accept()


def main():
    application = QApplication(sys.argv)
    setup_application(application)
    window = App()
    screen = application.primaryScreen().availableGeometry()
    window.resize(min(1510, screen.width() - 32), min(925, screen.height() - 45))
    window.show()
    return application.exec()


if __name__ == '__main__':
    sys.exit(main())
