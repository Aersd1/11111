"""Local lexical retrieval view. No agent, embeddings, or remote calls."""
import queue
import threading
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox,
    QSplitter, QTreeWidget, QTreeWidgetItem, QAbstractItemView, QHeaderView,
    QScrollArea, QPlainTextEdit, QApplication, QSizePolicy)
from ui_theme import label, button, card_layout
from local_search import BM25Index
from paper_links import decode_links
from ui_drag import start_paper_drag


class SearchTree(QTreeWidget):
    def startDrag(self, supported):
        start_paper_drag(self, [item.data(0, Qt.ItemDataRole.UserRole) for item in self.selectedItems()])


class SearchPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.rows, self.results = [], []
        self.index = BM25Index()
        self.generation, self.running = 0, False
        self.queue = queue.Queue()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(14)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter)
        frame, body = card_layout(margins=20)
        frame.setMinimumWidth(465)
        body.addWidget(label('搜索文献', 'sectionTitle'))
        body.addWidget(label('描述你想找的研究问题，从本地文献库找到相关论文。', 'muted', True))
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText('例如：大模型文献分类有哪些局限？')
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query, 1)
        self.limit = QComboBox()
        self.limit.addItems(['前 10 篇', '前 20 篇', '前 50 篇'])
        row.addWidget(self.limit)
        row.addWidget(button('搜索', self.search, 'primary', 'search'))
        body.addLayout(row)
        self.mode = QComboBox()
        self.mode.addItems(['本地 BM25 · 0 token', '智能 Agent · 问题改写 / 目录筛选 / 证据核对'])
        body.addWidget(self.mode)
        body.addWidget(label('智能模式每次最多 2 次模型调用，最多审阅 12 篇的短片段；使用设置中的 API。', 'muted', True))
        self.info = label('输入问题后点击搜索。检索摘要、结论、总结和个人描述。', 'muted', True)
        body.addWidget(self.info)
        self.tree = SearchTree()
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(['文献 / 匹配片段', '命中字段', '相关度'])
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(0, 390)
        self.tree.setColumnWidth(1, 140)
        self.tree.setColumnWidth(2, 90)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self.show_detail)
        self.tree.itemDoubleClicked.connect(lambda *_: host.open_selected())
        body.addWidget(self.tree, 1)
        controls = QHBoxLayout()
        self.open_button = button('打开文件', host.open_selected, 'soft', 'open')
        self.edit_button = button('编辑描述', host.edit_dialog, glyph='edit')
        controls.addWidget(self.open_button)
        controls.addWidget(self.edit_button)
        controls.addStretch()
        self.more_button = button('', host.more_menu, 'ghost', 'more')
        controls.addWidget(self.more_button)
        body.addLayout(controls)
        body.addWidget(button('补充旧文献结论（本地读取）', host.fill_missing_end, 'ghost', 'refresh'))
        self.splitter.addWidget(frame)
        frame, detail = card_layout(margins=20)
        frame.setMinimumWidth(270)
        detail.addWidget(label('文件与相关描述', 'sectionTitle'))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName('detailPage')
        self.detail = QVBoxLayout(content)
        self.detail.setContentsMargins(0, 12, 7, 12)
        self.detail.setSpacing(14)
        scroll.setWidget(content)
        detail.addWidget(scroll)
        self.splitter.addWidget(frame)
        self.splitter.setSizes([700, 330])
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.timeout.connect(self.search)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start(100)
        self.query.textChanged.connect(self.query_changed)
        self.mode.currentIndexChanged.connect(self.query_changed)
        self.show_detail()

    def query_changed(self):
        self.debounce.stop()
        self.generation += 1
        self.results = []
        self.render()
        self.info.setText('输入问题后点击搜索，或按 Enter 开始。')

    def set_rows(self, rows):
        self.rows = list(rows)
        self.generation += 1
        if self.query.text().strip() and self.mode.currentIndex() == 0:
            self.debounce.start(250)
        else:
            self.results = []
            self.render()

    def selected_ids(self):
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in self.tree.selectedItems()]

    def search(self):
        query = self.query.text().strip()
        if not query:
            return
        if self.running:
            if self.mode.currentIndex() == 0:
                self.debounce.start(200)
            return
        self.running = True
        self.generation += 1
        generation = self.generation
        rows = list(self.rows)
        limit = [10, 20, 50][self.limit.currentIndex()]
        intelligent = self.mode.currentIndex() == 1
        config_path = self.host.library.settings()['api_config']
        self.info.setText('正在改写问题、筛选目录并核对候选文献…' if intelligent else '正在本地检索…')
        def work():
            try:
                if intelligent:
                    from agent_search import agent_search
                    result, note = agent_search(rows, query[:1000], limit, config_path)
                    self.queue.put((generation, result, note))
                    return
                self.index.update(rows)
                result = self.index.search(query[:1000], limit)
                self.queue.put((generation, result, None))
            except Exception:
                self.queue.put((generation, [], '本地索引构建失败，请刷新后重试。'))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            generation, results, error = self.queue.get_nowait()
        except queue.Empty:
            return
        self.running = False
        if generation != self.generation:
            return
        self.results = results
        self.render()
        pending = sum(not p.get('end_checked') for p in self.rows)
        message = error or (f'找到 {len(results)} 篇相关文献。分数用于排序，不代表匹配概率。' if results else '没有找到匹配文献。可换用关键概念、中英文术语或补充个人描述。')
        if pending:
            message += f' 还有 {pending} 篇旧文献未读取结论。'
        self.info.setText(message)

    def render(self):
        selected = set(self.selected_ids())
        self.tree.blockSignals(True)
        self.tree.clear()
        for result in self.results:
            paper = result['paper']
            excerpt = result['evidence'][0]['text'] if result['evidence'] else ''
            item = QTreeWidgetItem([paper['title'] + '\n' + excerpt[:100].replace('\n', ' '),
                    '、'.join(e['field'] for e in result['evidence']), str(result['score'])])
            item.setData(0, Qt.ItemDataRole.UserRole, paper['id'])
            item.setToolTip(0, paper['title'] + '\n' + excerpt)
            self.tree.addTopLevelItem(item)
            if paper['id'] in selected:
                item.setSelected(True)
        if self.results and not self.tree.selectedItems():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.tree.blockSignals(False)
        self.show_detail()

    def show_detail(self):
        ids = self.selected_ids()
        found = next((r for r in self.results if ids and r['paper']['id'] == ids[0]), None)
        self.open_button.setEnabled(bool(found))
        self.edit_button.setEnabled(bool(found) and not self.host.busy)
        while self.detail.count():
            item = self.detail.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if found:
            paper = found['paper']
            fields = [(paper['title'], 'sectionTitle'), (paper['major'] + ' / ' + paper['minor'], 'muted'),
                      ('个人描述', 'eyebrow'), (paper.get('description') or '尚未填写，可点击“编辑描述”补充。', None),
                      ('为什么匹配', 'eyebrow')]
            if found.get('agent_reason'):
                fields.append(('模型关联说明：' + found['agent_reason'], None))
            for evidence in found['evidence']:
                fields.append((evidence['field'] + '：' + evidence['text'], None))
            fields.extend([('总结', 'eyebrow'), (paper['summary'] or '尚未整理', None)])
        else:
            fields = [('找到值得阅读的论文', 'sectionTitle'), ('搜索结果只来自本地库；没有命中时不会编造文件或描述。', 'muted')]
        for text, role in fields:
            value = label(text, role, True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.detail.addWidget(value)
        if found:
            self.detail.addWidget(label('原文件位置', 'eyebrow'))
            path_box = QPlainTextEdit(paper['path'])
            path_box.setReadOnly(True)
            path_box.setFixedHeight(85)
            self.detail.addWidget(path_box)
            for link in decode_links(paper.get('links')):
                item = button(link['label'], lambda url=link['url']: self.host.open_reference(url), 'soft', 'open')
                item.setToolTip(link['url'])
                self.detail.addWidget(item)
        self.detail.addStretch()
