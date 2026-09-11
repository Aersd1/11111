"""Collapsible, incrementally populated literature directory."""
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (QTreeWidget, QTreeWidgetItem, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QSplitter, QAbstractItemView, QHeaderView, QScrollArea, QSizePolicy, QPlainTextEdit, QTreeWidgetItemIterator)
from ui_theme import icon, label, button, card_layout
from ui_drag import start_paper_drag
from folder_paths import parts, packed, ancestors, contains, ui_key

ROLE = Qt.ItemDataRole.UserRole


class FolderNameEdit(QLineEdit):
    cancelled = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class FoldTree(QTreeWidget):
    """A group toggles once whether the label or its arrow is clicked."""
    def __init__(self):
        super().__init__()
        self.setRootIsDecorated(False)
        self.setExpandsOnDoubleClick(False)
        self.itemExpanded.connect(lambda item: item.setIcon(0, icon('chevron_down')))
        self.itemCollapsed.connect(lambda item: item.setIcon(0, icon('chevron_right')))

    @staticmethod
    def is_group(item):
        return item and item.childIndicatorPolicy() == QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and self.is_group(item):
            self.setCurrentItem(item)
            item.setExpanded(not item.isExpanded())
            self.itemClicked.emit(item, 0)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.is_group(self.itemAt(event.position().toPoint())):
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return) and self.is_group(self.currentItem()):
            item = self.currentItem()
            item.setExpanded(not item.isExpanded())
            event.accept()
        else:
            super().keyPressEvent(event)

    def startDrag(self, supported):
        ids = [item.data(0, ROLE)[1] for item in self.selectedItems()
               if item.data(0, ROLE) and item.data(0, ROLE)[0] == 'paper']
        start_paper_drag(self, ids)


def group_item(text, key):
    item = QTreeWidgetItem([text])
    item.setData(0, ROLE, key)
    item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
    item.setIcon(0, icon('chevron_right'))
    item.setToolTip(0, '点击展开 / 收起；也可使用左右方向键')
    return item


class CatalogPage(QWidget):
    layout_changed = Signal()
    BATCH = 100

    def __init__(self, host, state=None):
        super().__init__()
        self.host = host
        self.rows = []
        self.by_id = {}
        self.groups = {}
        self.expanded = {tuple(k) for k in (state or {}).get('expanded', [])}
        self.loaded = {}
        self.rebuilding = False
        self.folder_edit = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(14)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.splitterMoved.connect(lambda *_: self.layout_changed.emit())
        outer.addWidget(self.splitter)
        frame, layout = card_layout(margins=20)
        frame.setMinimumWidth(465)
        head = QHBoxLayout()
        head.addWidget(label('文献目录', 'sectionTitle'))
        self.count = label('', 'muted')
        head.addWidget(self.count)
        head.addStretch()
        head.addWidget(button('新建文件夹', host.create_category_dialog, 'soft', 'plus'))
        head.addWidget(button('打开磁盘目录', self.open_disk, 'ghost'))
        head.addWidget(button('全部收起', self.collapse_all, 'ghost'))
        layout.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText('在目录中搜索题目、关键词或摘要…')
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon('search'), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self.rebuild)
        layout.addWidget(self.search)
        layout.addWidget(label('支持多层文件夹 · 添加可在任意层新建子文件夹 · 双击文献打开原文', 'muted', True))
        self.tree = FoldTree()
        self.tree.setRootIsDecorated(True)
        self.tree.setStyleSheet('QTreeWidget::item { padding: 0px 6px; height: 42px; }')
        self.tree.itemExpanded.connect(lambda item: item.setIcon(0, icon('folder', '#ac86df')))
        self.tree.itemCollapsed.connect(lambda item: item.setIcon(0, icon('folder', '#ac86df')))
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.tree.setAcceptDrops(True)
        self.tree.viewport().setAcceptDrops(True)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(['文件夹 / 文献', '文件夹操作', '状态', '添加日期'])
        self.tree.setIndentation(20)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.header().setStretchLastSection(False)
        self.tree.setColumnWidth(0, 390)
        self.tree.setColumnWidth(1, 210)
        self.tree.setColumnWidth(2, 110)
        self.tree.setColumnWidth(3, 115)
        for column, width in enumerate((state or {}).get('columns', [])[:4]):
            self.tree.setColumnWidth(column, width)
        self.tree.setColumnWidth(1, 210)
        self.tree.setColumnHidden(2, True)
        self.tree.setColumnHidden(3, True)
        self.tree.header().sectionResized.connect(lambda *_: self.layout_changed.emit())
        self.tree.itemExpanded.connect(self.on_expanded)
        self.tree.itemCollapsed.connect(self.on_collapsed)
        self.tree.itemSelectionChanged.connect(self.show_detail)
        self.tree.itemClicked.connect(self.item_clicked)
        self.tree.itemDoubleClicked.connect(self.open_item)
        layout.addWidget(self.tree, 1)
        self.folder_hint = label('', 'muted', True)
        self.folder_hint.hide()
        layout.addWidget(self.folder_hint)
        self.empty = label('没有匹配的文献，可调整搜索或侧栏筛选。', 'muted', True)
        layout.addWidget(self.empty)
        actions = QHBoxLayout()
        self.open_button = button('打开', host.open_selected, 'soft', 'open')
        self.edit_button = button('编辑', host.edit_dialog, glyph='edit')
        self.organize_button = button('整理所选', host.process_selected, glyph='spark')
        for control in (self.open_button, self.edit_button, self.organize_button):
            actions.addWidget(control)
        actions.addStretch()
        self.more_button = button('', host.more_menu, 'ghost', 'more')
        self.more_button.setToolTip('打开方式、重新关联、移除索引和导出')
        actions.addWidget(self.more_button)
        layout.addLayout(actions)
        self.splitter.addWidget(frame)
        frame, layout = card_layout(margins=20)
        frame.setMinimumWidth(270)
        layout.addWidget(label('阅读预览', 'sectionTitle'))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        page = QWidget()
        page.setObjectName('detailPage')
        self.detail_layout = QVBoxLayout(page)
        self.detail_layout.setContentsMargins(0, 10, 8, 10)
        self.detail_layout.setSpacing(16)
        self.scroll.setWidget(page)
        layout.addWidget(self.scroll, 1)
        self.splitter.addWidget(frame)
        self.splitter.setSizes((state or {}).get('sizes', [730, 300]))
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)

    def selected_ids(self):
        return [item.data(0, ROLE)[1] for item in self.tree.selectedItems()
                if item.data(0, ROLE) and item.data(0, ROLE)[0] == 'paper']

    def set_rows(self, rows):
        self.rows = rows
        self.by_id = {p['id']: p for p in rows}
        self.rebuild()

    def rebuild(self, *args):
        if self.folder_edit is not None:
            return  # Preserve the input and IME composition across background refreshes.
        selected = set(self.selected_ids())
        old_scroll = self.tree.verticalScrollBar().value()
        previous_loaded = dict(self.loaded)
        self.rebuilding = True
        self.tree.blockSignals(True)
        self.tree.clear()
        self.groups = defaultdict(list)
        self.loaded = {}
        query = self.search.text().strip().casefold()
        for paper in self.rows:
            if query and query not in ' '.join(str(paper.get(k, '')) for k in ('title', 'keywords', 'abstract', 'summary', 'major', 'minor', 'path', 'description', 'conclusion', 'limitations')).casefold():
                continue
            self.groups[(paper['major'], paper['minor'])].append(paper)
        keys = {parent for key in self.groups for parent in ancestors(key)}
        if not query and not self.host.only_pending:
            for key in self.host.library.directory_keys():
                if not self.host.category or contains(self.host.category, key):
                    keys.update(ancestors(key))
        self.folder_items = {}
        counts = defaultdict(int)
        for key, papers in self.groups.items():
            for parent in ancestors(key):
                counts[parent] += len(papers)
        total = sum(len(p) for p in self.groups.values())
        for key in sorted(keys, key=parts):
            path = parts(key)
            identity = ('minor', *key) if key[1] else ('major', key[0])
            item = group_item(f'{path[-1]}    {counts[key]} 篇', identity)
            if len(path) == 1:
                self.tree.addTopLevelItem(item)
            else:
                self.folder_items[ui_key(packed(path[:-1]))].addChild(item)
            self.folder_items[ui_key(key)] = item
            self.folder_actions(item, ui_key(key))
        for item in self.folder_items.values():
            identity = tuple(item.data(0, ROLE))
            is_open = bool(query) or identity in self.expanded
            if is_open:
                self.populate(item, max(self.BATCH, previous_loaded.get(identity, 0)))
            item.setExpanded(is_open)
            item.setIcon(0, icon('folder', '#ac86df'))
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            entry = iterator.value()
            value = entry.data(0, ROLE)
            if value and value[0] == 'paper' and value[1] in selected:
                entry.setSelected(True)
            iterator += 1
        self.tree.blockSignals(False)
        self.rebuilding = False
        self.tree.verticalScrollBar().setValue(old_scroll)
        self.count.setText(f'{total} 篇 · {len(self.folder_items)} 个文件夹')
        self.empty.setVisible(total == 0)
        self.show_detail()

    def folder_item(self, key):
        return getattr(self, 'folder_items', {}).get(ui_key(key))

    def reveal_folder(self, item):
        parent = item.parent()
        while parent:
            parent.setExpanded(True)
            parent = parent.parent()

    def prepare_folder_edit(self):
        if self.folder_edit is not None and not self.finish_folder_edit():
            return False
        self.host.set_page('catalog')
        self.host.category = None
        self.host.only_pending = False
        self.search.clear()
        self.host.refresh()
        return True

    def begin_new_folder(self, parent_major=None):
        if not self.prepare_folder_edit():
            return
        parent_key = (parent_major,) if isinstance(parent_major, str) else parent_major
        parent = self.folder_item(parent_key) if parent_key else None
        if parent_major and parent is None:
            self.host.status.setText('所在文件夹已不存在，请刷新后重试。')
            return
        parent_path = parts(parent_key) if parent_key else ()
        names = {parts(key)[-1].casefold() for key in self.host.library.directory_keys() if parts(key)[:-1] == parent_path}
        name, number = '新建文件夹', 2
        while name.casefold() in names:
            name = f'新建文件夹 ({number})'
            number += 1
        item = QTreeWidgetItem([name])
        item.setData(0, ROLE, ('draft',))
        item.setIcon(0, icon('folder', '#ac86df'))
        if parent:
            self.reveal_folder(parent)
            parent.setExpanded(True)
            parent.insertChild(0, item)
        else:
            self.tree.insertTopLevelItem(0, item)
        self.start_folder_edit(item, name, parent_major=parent_key)

    def begin_rename_folder(self, key):
        if not self.prepare_folder_edit():
            return
        item = self.folder_item(key)
        if item:
            self.reveal_folder(item)
            self.start_folder_edit(item, parts(key)[-1], rename_key=tuple(key))

    def start_folder_edit(self, item, name, parent_major=None, rename_key=None):
        editor = FolderNameEdit(name)
        editor.setObjectName('folderNameEditor')
        editor.setMaxLength(80)
        editor.setFixedHeight(32)
        self.folder_edit = {'editor': editor, 'item': item, 'parent': parent_major, 'rename': rename_key}
        self.tree.setItemWidget(item, 0, editor)
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        self.empty.hide()
        self.folder_hint.setText('直接输入名称 · Enter 保存 · Esc 取消')
        self.folder_hint.show()
        editor.editingFinished.connect(self.finish_folder_edit)
        editor.cancelled.connect(self.cancel_folder_edit)
        editor.setFocus(Qt.FocusReason.OtherFocusReason)
        editor.selectAll()

    def finish_folder_edit(self):
        edit = self.folder_edit
        if edit is None:
            return True
        try:
            name = self.host.library.category_name(edit['editor'].text())
            if self.host.busy:
                raise ValueError('正在整理文献，请等待完成后再保存名称。')
            if edit['rename']:
                key = edit['rename']
                self.host.library.rename_category(key[0], key[1] if len(key) > 1 else '', name)
                target = packed((*parts(key)[:-1], name))
            else:
                parent = edit['parent']
                categories = self.host.library.directory_keys()
                target = packed((*(parts(parent) if parent else ()), name))
                exists = any(tuple(n.casefold() for n in key) == tuple(n.casefold() for n in target) for key in categories)
                if exists:
                    raise ValueError('同级文件夹已存在，请输入其他名称。')
                self.host.library.create_category(*target)
        except (ValueError, OSError) as exc:
            self.folder_hint.setText(str(exc) + ' · Esc 取消')
            editor = edit['editor']
            QTimer.singleShot(0, lambda: editor.setFocus() if self.folder_edit is edit else None)
            return False
        self.folder_edit = None
        self.folder_hint.hide()
        self.host.refresh()
        item = self.folder_item(target)
        if item:
            self.reveal_folder(item)
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
        return True

    def cancel_folder_edit(self):
        self.folder_edit = None
        self.folder_hint.hide()
        self.host.refresh()

    def open_disk(self):
        import os
        from disk_catalog import sync_catalog
        try:
            from platform_support import open_native
            open_native(sync_catalog(self.host.library))
        except (OSError, ValueError) as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, '无法打开目录', str(exc))

    def folder_actions(self, item, key):
        panel = QWidget()
        row = QHBoxLayout(panel)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(2)
        add = lambda: self.host.create_category_dialog(key[0] if len(key) == 1 else key)
        for text, callback in [('添加', add), ('删除', lambda: self.host.delete_category_dialog(key)),
                               ('重命名', lambda: self.host.rename_category_dialog(key))]:
            control = button(text, callback, 'ghost')
            control.setStyleSheet('padding:4px 6px; min-width:0;')
            control.setFixedHeight(28)
            control.setEnabled(not self.host.busy)
            row.addWidget(control)
        self.tree.setItemWidget(item, 1, panel)
        item.setToolTip(0, '磁盘分类文件夹；拖入文献以调整快捷方式的位置')

    def add_to_folder(self, key):
        from PySide6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(self, '添加文献到此文件夹', '', '文献 (*.pdf *.docx *.txt *.md)')
        if paths:
            self.host.import_paths(paths, target_category=key)

    def populate(self, item, amount=None):
        key = tuple(item.data(0, ROLE))
        papers = self.groups.get((key[1], '') if key[0] == 'major' else tuple(key[1:]), [])
        start = self.loaded.get(key, 0)
        if item.childCount() and item.child(item.childCount() - 1).data(0, ROLE)[0] == 'more':
            item.takeChild(item.childCount() - 1)
        stop = min(len(papers), start + (amount or self.BATCH))
        for paper in papers[start:stop]:
            state = paper['status'] if Path(paper['path']).is_file() else '文件失联'
            leaf = QTreeWidgetItem([paper['title'], state, state, paper['added'][:10]])
            leaf.setData(0, ROLE, ('paper', paper['id']))
            leaf.setIcon(0, icon('doc', '#9366de'))
            leaf.setToolTip(0, paper['title'])
            leaf.setToolTip(1, paper['keywords'])
            item.addChild(leaf)
        self.loaded[key] = stop
        if stop < len(papers):
            more = QTreeWidgetItem([f'显示更多…（已显示 {stop} / {len(papers)} 篇）'])
            more.setData(0, ROLE, ('more',))
            item.addChild(more)
            more.setFirstColumnSpanned(True)

    def on_expanded(self, item):
        key = tuple(item.data(0, ROLE))
        if key[0] in ('major', 'minor') and not self.loaded.get(key):
            self.populate(item)
        if not self.rebuilding:
            self.expanded.add(key)
            self.layout_changed.emit()

    def on_collapsed(self, item):
        if not self.rebuilding:
            self.expanded.discard(tuple(item.data(0, ROLE)))
            self.layout_changed.emit()

    def item_clicked(self, item, column):
        if item.data(0, ROLE)[0] == 'more':
            self.populate(item.parent())

    def open_item(self, item, column):
        if item.data(0, ROLE)[0] == 'paper':
            self.host.open_selected()

    def expand_majors(self):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setExpanded(True)

    def collapse_all(self):
        self.tree.collapseAll()
        self.expanded.clear()
        self.layout_changed.emit()

    def state(self):
        return {'expanded': [list(key) for key in sorted(self.expanded)], 'sizes': self.splitter.sizes(),
                'columns': [self.tree.columnWidth(i) for i in range(4)]}

    def show_detail(self):
        ids = self.selected_ids()
        paper = self.by_id.get(ids[0]) if ids else None
        for control in (self.open_button, self.edit_button, self.organize_button):
            control.setEnabled(bool(paper) and (not self.host.busy or control == self.open_button))
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if paper:
            fields = [(paper['title'], 'sectionTitle'), (paper['major'] + ' / ' + paper['minor'], 'muted'),
                      ('研究总结', 'eyebrow'), (paper['summary'] or '尚未整理', None),
                      ('个人描述', 'eyebrow'), (paper.get('description') or '尚未填写', None),
                      ('摘要', 'eyebrow'), (paper['abstract'] or '尚未识别，可点击编辑补充。', None),
                      ('结论', 'eyebrow'), (paper.get('conclusion') or '尚未识别，可读取结论或手动补充。', None),
                      ('局限', 'eyebrow'), (paper.get('limitations') or '未识别独立局限段，请结合结论核对。', None),
                      ('原文件', 'eyebrow'), (paper['path'], 'muted')]
        else:
            fields = [('按目录浏览文献', 'sectionTitle'), ('逐层展开文件夹，再选择文献查看总结与摘要。每个文件夹的“添加”都可以创建子文件夹。', 'muted')]
        for text, style in fields:
            if paper and text == paper['path']:
                path_box = QPlainTextEdit(text)
                path_box.setReadOnly(True)
                path_box.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
                path_box.setFixedHeight(80)
                path_box.setStyleSheet('background: white; border:none; padding:0; color:#7c8098; font-size:13px;')
                self.detail_layout.addWidget(path_box)
                continue
            widget = label(text, style, True)
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.detail_layout.addWidget(widget)
        if paper:
            from paper_links import decode_links
            for link in decode_links(paper.get('links')):
                self.detail_layout.addWidget(button(link['label'], lambda url=link['url']: self.host.open_reference(url), 'soft', 'open'))
        self.detail_layout.addStretch()
