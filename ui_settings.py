import copy
import json
import queue
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QTabWidget, QWidget, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QPlainTextEdit,
    QFileDialog, QMessageBox, QScrollArea, QAbstractSpinBox)

from api_settings import read_config, validate_config, write_config
from library_core import call_model_config
from ui_theme import label, button
from platform_support import open_native, valid_program
from fulltext_agent import QUESTIONS, questions_from
import sys


class SettingsDialog(QDialog):
    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.settings = library.settings()
        self.document = {'OpenAI': {}}
        self.dirty = False
        self.api_dirty = False
        self.loading = True
        self.testing = False
        self.results = queue.Queue()
        self.setWindowTitle('设置 · 文献书架')
        self.resize(810, 770)
        self.setMinimumSize(700, 650)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)
        layout.addWidget(label('工作空间设置', 'heading'))
        layout.addWidget(label('配置阅读习惯与模型接口，让文献整理更顺手。', 'muted'))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        api = QWidget()
        api.setObjectName('settingsPage')
        form = QVBoxLayout(api)
        form.setContentsMargins(2, 20, 2, 8)
        form.setSpacing(12)
        form.addWidget(label('接口配置文件', 'sectionTitle'))
        row = QHBoxLayout()
        self.path = QLineEdit(self.settings['api_config'])
        self.path.setReadOnly(True)
        self.path.setToolTip(self.settings['api_config'])
        row.addWidget(self.path, 1)
        row.addWidget(button('打开文件…', self.choose_file, glyph='folder'))
        form.addLayout(row)
        links = QHBoxLayout()
        links.addWidget(button('编辑原始 JSON', self.edit_json, 'ghost', 'edit'))
        links.addWidget(button('用系统编辑器打开', self.open_external, 'ghost', 'open'))
        links.addWidget(button('重新读取', self.reload_file, 'ghost', 'refresh'))
        links.addStretch()
        form.addLayout(links)
        fields = QFormLayout()
        fields.setVerticalSpacing(13)
        fields.setHorizontalSpacing(24)
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText('https://your-provider.example/v1')
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText('输入 API Key')
        self.model = QLineEdit()
        self.model.setPlaceholderText('服务商提供的模型名称')
        fields.addRow('Base URL', self.base_url)
        keyrow = QHBoxLayout()
        keyrow.addWidget(self.api_key, 1)
        reveal = QCheckBox('显示')
        reveal.toggled.connect(lambda checked: self.api_key.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        keyrow.addWidget(reveal)
        fields.addRow('API Key', keyrow)
        fields.addRow('模型名称', self.model)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0, 2)
        self.temperature.setSingleStep(0.1)
        self.temperature.setDecimals(2)
        self.temperature.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.maximum = QSpinBox()
        self.maximum.setRange(1, 1000000)
        self.maximum.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        fields.addRow('Temperature', self.temperature)
        fields.addRow('Max tokens', self.maximum)
        self.enable_thinking = QCheckBox('启用思考（默认开启）')
        self.enable_thinking.setChecked(True)
        self.enable_thinking.setToolTip('对普通整理、全文分析和智能检索生效。当前支持国家超算互联网的 DeepSeek-V4 / Qwen3；其他接口沿用服务端行为。关闭可减少生成等待。')
        fields.addRow('思考模式', self.enable_thinking)
        form.addLayout(fields)
        form.addWidget(label('支持 OpenAI 兼容接口。填写 Base URL，无需添加 /chat/completions。\n整理使用流式接收，单次输出最多 16384 tokens；其他已有配置字段会保留。', 'muted', True))
        testrow = QHBoxLayout()
        self.test_button = button('测试当前配置', self.test_connection, 'soft', 'spark')
        testrow.addWidget(self.test_button)
        self.api_status = label('尚未测试', 'muted', True)
        testrow.addWidget(self.api_status, 1)
        form.addLayout(testrow)
        form.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(api)
        self.tabs.addTab(scroll, '模型接口')
        general = QWidget()
        general_layout = QVBoxLayout(general)
        general_layout.setContentsMargins(2, 24, 2, 10)
        general_layout.setSpacing(16)
        prefs = QFormLayout()
        prefs.setVerticalSpacing(20)
        self.mode = QComboBox()
        self.mode.addItems(['大模型', '本地规则', '手动'])
        self.mode.setCurrentText(self.settings['mode'])
        self.opener = QComboBox()
        self.opener.addItems(['系统默认', '浏览器', '指定程序'])
        self.opener.setCurrentText(self.settings['opener'])
        self.program = QLineEdit(self.settings['program'])
        program_row = QHBoxLayout()
        program_row.addWidget(self.program)
        program_row.addWidget(button('选择程序…', self.choose_program))
        prefs.addRow('默认整理方式', self.mode)
        prefs.addRow('默认打开方式', self.opener)
        prefs.addRow('指定阅读程序', program_row)
        general_layout.addLayout(prefs)
        general_layout.addWidget(label('添加文件后按默认方式整理。手动与本地规则模式不联网，使用原文摘录。\n\n大模型首次整理即综合题目、摘要、关键词、引言前两段、结论和局限段，一次请求完成。原文件不上传，个人描述和链接不发送给模型。', 'muted', True))
        general_layout.addStretch()
        self.tabs.addTab(general, '阅读与整理')
        analysis_page = QWidget()
        analysis_layout = QVBoxLayout(analysis_page)
        analysis_layout.addWidget(label('全文 Agent · 默认分析问题', 'sectionTitle'))
        analysis_layout.addWidget(label('每行一个预设问题。全文分析一次提交完整可提取正文，只回答这些问题及各篇的补充问题，不再逐段写笔记或请求回读。', 'muted', True))
        self.analysis_questions = QPlainTextEdit(self.settings.get('analysis_questions', '\n'.join(QUESTIONS)))
        analysis_layout.addWidget(self.analysis_questions, 1)
        analysis_layout.addWidget(button('恢复论文十问', lambda: self.analysis_questions.setPlainText('\n'.join(QUESTIONS)), 'ghost'))
        analysis_layout.addWidget(label('每篇最多一次请求；复用相同问题的完整回答时不调用模型。失败后由你手动重试。全文不截断，也不自动拆成多次请求；需要模型支持相应的上下文长度。思考模式沿用“模型接口”设置。', 'muted', True))
        self.tabs.addTab(analysis_page, '全文分析')
        self.analysis_questions.textChanged.connect(self.mark_dirty)
        rules_page = QWidget()
        rules_layout = QVBoxLayout(rules_page)
        rules_layout.setContentsMargins(2, 20, 2, 8)
        rules_layout.addWidget(label('本地两级分类规则', 'sectionTitle'))
        rules_layout.addWidget(label('major：大类  ·  minor：小类  ·  terms：匹配词列表', 'muted'))
        self.rules = QPlainTextEdit(json.dumps(self.settings['rules'], ensure_ascii=False, indent=2))
        self.rules.setStyleSheet('font-family: Consolas; font-size: 13px;')
        rules_layout.addWidget(self.rules, 1)
        self.tabs.addTab(rules_page, '分类规则')
        bottom = QHBoxLayout()
        bottom.addWidget(label('保存接口前自动保留一份 .bak 备份。', 'muted'))
        bottom.addStretch()
        bottom.addWidget(button('取消', self.reject))
        bottom.addWidget(button('保存并使用', self.save_and_use, 'primary', 'check'))
        layout.addLayout(bottom)
        for field in (self.base_url, self.api_key, self.model):
            field.textChanged.connect(self.mark_api)
        self.temperature.valueChanged.connect(self.mark_api)
        self.maximum.valueChanged.connect(self.mark_api)
        self.enable_thinking.toggled.connect(self.mark_api)
        for field in (self.mode, self.opener):
            field.currentTextChanged.connect(self.mark_dirty)
        self.program.textChanged.connect(self.mark_dirty)
        self.rules.textChanged.connect(self.mark_dirty)
        try:
            self.populate(read_config(self.path.text()))
            self.api_status.setText('已读取现有配置，修改后点击“保存并使用”。')
        except ValueError as exc:
            self.populate({'OpenAI': {}})
            self.api_status.setText(str(exc))
        self.loading = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_test)
        self.timer.start(120)

    def mark_api(self, *args):
        if not self.loading:
            self.api_dirty = self.dirty = True
            if not self.testing:
                self.api_status.setText('配置已修改，尚未保存。')

    def mark_dirty(self, *args):
        if not self.loading:
            self.dirty = True

    def populate(self, document):
        before = self.loading
        self.loading = True
        self.document = copy.deepcopy(document)
        cfg = document['OpenAI']
        self.base_url.setText(cfg.get('base_url', ''))
        self.api_key.setText(cfg.get('api_key', ''))
        self.model.setText(cfg.get('model', ''))
        self.maximum.setValue(cfg.get('max_tokens', 2500))
        self.temperature.setValue(cfg.get('temperature', 0.2))
        self.enable_thinking.setChecked(cfg.get('enable_thinking', True))
        self.loading = before

    def candidate(self):
        document = copy.deepcopy(self.document)
        document['OpenAI'].update(base_url=self.base_url.text().strip().rstrip('/'), api_key=self.api_key.text().strip(),
                                  model=self.model.text().strip(), temperature=self.temperature.value(), max_tokens=self.maximum.value(),
                                  enable_thinking=self.enable_thinking.isChecked())
        return validate_config(document)

    def discard_api(self):
        return not self.api_dirty or QMessageBox.question(self, '尚未保存', '舍弃当前接口修改，重新读取文件？') == QMessageBox.StandardButton.Yes

    def choose_file(self):
        if not self.discard_api():
            return
        path, _ = QFileDialog.getOpenFileName(self, '打开 API 配置', str(Path(self.path.text()).parent), 'JSON 配置 (*.json)')
        if path:
            self.load_file(path)

    def load_file(self, path):
        try:
            document = read_config(path)
        except ValueError as exc:
            QMessageBox.warning(self, '无法读取', str(exc))
            return
        self.populate(document)
        self.path.setText(str(Path(path).resolve()))
        self.path.setToolTip(self.path.text())
        self.api_dirty = False
        self.dirty = True
        self.api_status.setText('已载入文件，点击“保存并使用”切换到此接口。')

    def reload_file(self):
        if self.discard_api():
            self.load_file(self.path.text())

    def open_external(self):
        if not Path(self.path.text()).is_file():
            QMessageBox.warning(self, '文件不存在', '请先打开一个现有配置文件。')
            return
        try:
            open_native(self.path.text(), edit=True)
            self.api_status.setText('已用系统编辑器打开；外部保存后请点击“重新读取”。')
        except OSError:
            QMessageBox.warning(self, '无法打开编辑器', '可以使用“编辑原始 JSON”在此修改。')

    def edit_json(self):
        editor = QDialog(self)
        editor.setWindowTitle('编辑 API JSON')
        editor.resize(750, 590)
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.addWidget(label('编辑原始配置', 'sectionTitle'))
        layout.addWidget(label('此处包含完整密钥。应用到表单后，点击“保存并使用”写入文件。', 'muted', True))
        try:
            document = self.candidate()
        except ValueError:
            document = copy.deepcopy(self.document)
            document['OpenAI'].update(base_url=self.base_url.text(), api_key=self.api_key.text(), model=self.model.text())
        text = QPlainTextEdit(json.dumps(document, ensure_ascii=False, indent=2))
        text.setStyleSheet('font-family: Consolas; font-size: 14px;')
        layout.addWidget(text, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button('取消', editor.reject))
        def apply():
            try:
                parsed = validate_config(json.loads(text.toPlainText()))
            except (ValueError, TypeError):
                QMessageBox.warning(editor, '配置无效', '请检查 JSON 格式，以及 OpenAI 下的地址、密钥、模型和数值字段。')
                return
            self.populate(parsed)
            self.mark_api()
            editor.accept()
        actions.addWidget(button('应用到表单', apply, 'primary'))
        layout.addLayout(actions)
        editor.exec()

    def choose_program(self):
        if sys.platform == 'darwin':
            path = QFileDialog.getExistingDirectory(self, '选择阅读程序 .app', '/Applications')
        else:
            path, _ = QFileDialog.getOpenFileName(self, '选择默认阅读程序', '', '程序 (*.exe)' if sys.platform == 'win32' else '可执行程序 (*)')
        if path:
            self.program.setText(path)

    def test_connection(self):
        if self.testing:
            return
        try:
            document = self.candidate()
        except ValueError as exc:
            self.api_status.setText(str(exc))
            return
        self.testing = True
        self.test_button.setEnabled(False)
        self.test_button.setText('正在测试…')
        self.api_status.setText('使用一段测试摘要请求当前模型，连接或连续无数据时最长等待 180 秒，临时网络错误最多重试两次。')
        def work():
            try:
                call_model_config(document, {'title': 'Document classification using language models',
                    'abstract': 'This is a synthetic connection test about classifying documents with a language model.',
                    'keywords': 'language model; classification', 'existing_categories': []})
                outcome = (True, '连接成功，模型已返回有效的分类和总结。', document)
            except Exception as exc:
                outcome = (False, str(exc) if isinstance(exc, RuntimeError) else '测试失败，请检查网络或接口配置。', document)
            self.results.put(outcome)
        threading.Thread(target=work, daemon=True).start()

    def poll_test(self):
        try:
            ok, message, tested = self.results.get_nowait()
        except queue.Empty:
            return
        self.testing = False
        self.test_button.setEnabled(True)
        self.test_button.setText('测试当前配置')
        try:
            changed = self.candidate() != tested
        except ValueError:
            changed = True
        self.api_status.setText(message + (' 当前表单已修改，请重新测试。' if changed else ''))

    def save_and_use(self):
        try:
            questions_from(self.analysis_questions.toPlainText())
            rules = json.loads(self.rules.toPlainText())
            if not isinstance(rules, list) or not all(isinstance(r, dict) and all(isinstance(r.get(k), str) and r[k].strip() for k in ('major', 'minor')) and isinstance(r.get('terms'), list) and all(isinstance(t, str) and t.strip() for t in r['terms']) for r in rules):
                raise ValueError('分类规则需要 major、minor 和 terms 字符串列表。')
            if self.opener.currentText() == '指定程序':
                program = Path(self.program.text().strip())
                if not valid_program(program):
                    raise ValueError('请选择有效的阅读程序。')
            if self.mode.currentText() == '大模型' or self.api_dirty:
                document = self.candidate()
                if self.api_dirty or not Path(self.path.text()).is_file():
                    write_config(self.path.text(), document)
            self.library.save_settings({**self.settings, 'api_config': self.path.text(), 'mode': self.mode.currentText(),
                'opener': self.opener.currentText(), 'program': self.program.text().strip(), 'rules': rules,
                'analysis_questions': self.analysis_questions.toPlainText()})
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, '无法保存', str(exc))
            return
        self.dirty = False
        self.accept()

    def reject(self):
        if self.dirty and QMessageBox.question(self, '尚未保存', '放弃未保存的设置并关闭？') != QMessageBox.StandardButton.Yes:
            return
        super().reject()
