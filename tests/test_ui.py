import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from library_core import Library
from ui_settings import SettingsDialog
from ui_theme import STYLE, setup_application
from api_settings import read_config
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QDialog, QTabWidget, QPushButton
from PySide6.QtCore import Qt, QPoint, QPointF, QMimeData, QUrl, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QDragLeaveEvent
from PySide6.QtTest import QTest

QAPP = QApplication.instance() or QApplication([])
setup_application(QAPP)
QAPP.setStyleSheet(STYLE)


class UITests(unittest.TestCase):
    def test_batch_model_requests_overlap_and_one_failure_preserves_summary(self):
        self.lib.save_settings({**self.lib.settings(), 'mode': '大模型'})
        ids = self.ids[:3]
        for ident in ids:
            self.lib.update(ident, abstract='evidence', end_checked=1, summary='saved summary')
        gate = threading.Barrier(2)
        lock = threading.Lock()
        active = peak = 0
        def model(paper, settings, categories):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                if paper['id'] in ids[:2]:
                    gate.wait(timeout=3)
                else:
                    raise RuntimeError('model unavailable')
                return {'major': 'AI', 'minor': 'Test', 'summary': 'new summary', 'basis': 'all sections', 'status': '模型已整理'}
            finally:
                with lock:
                    active -= 1
        window = App(self.lib)
        with patch('app.organize', side_effect=model):
            window.process(ids)
            deadline = time.monotonic() + 6
            while window.busy and time.monotonic() < deadline:
                QAPP.processEvents()
                window.poll()
                time.sleep(.01)
        self.assertFalse(window.busy)
        self.assertEqual(peak, 2)
        self.assertTrue(all(self.lib.get(i)['summary'] == 'new summary' for i in ids[:2]))
        self.assertEqual(self.lib.get(ids[2])['summary'], 'saved summary')
        self.assertEqual(self.lib.get(ids[2])['status'], '处理失败')
        window.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / 'api.json'
        self.config.write_text(json.dumps({'OpenAI': {'base_url': 'https://example.invalid/v1', 'api_key': 'test-only-key', 'model': 'example-model', 'embedding_model': 'preserved'}}))
        self.lib = Library(self.root / 'library.sqlite3')
        self.lib.save_settings({**self.lib.settings(), 'api_config': str(self.config), 'mode': '本地规则'})
        self.ids = []
        fixture = [('检索增强生成中的文献知识组织方法', '人工智能', '知识检索', '模型已整理'),
                   ('面向储能应用的电池材料性能研究', '材料科学', '电池与储能', '规则已整理'),
                   ('多模态模型的视觉语义对齐研究', '人工智能', '计算机视觉', '模型已整理'),
                   ('跨学科研究的数据分析框架', '待分类', '待确认', '待补充摘要'),
                   ('基于大语言模型的科学文献自动分类方法', '人工智能', '大语言模型', '模型已整理')]
        for index, (title, major, minor, state) in enumerate(fixture):
            paper = self.root / f'example-{index}.txt'
            paper.write_text('Example', encoding='utf-8')
            paper_id, _ = self.lib.add(paper)
            self.ids.append(paper_id)
            self.lib.update(paper_id, title=title, major=major, minor=minor, status=state,
                abstract='【界面测试数据】研究基于题目、摘要和关键词的文献分类方法。', keywords='文献分类；大语言模型；知识管理',
                summary='【界面测试数据】\n\n研究问题\n如何根据文献的有限信息，建立清晰的两级分类。\n\n研究方法\n结合题目、摘要与关键词进行主题识别，信息不足时补充引言。\n\n主要发现\n示例内容不包含真实实验结果。',
                basis='题目、摘要、关键词 / 测试数据')
        self.window = App(self.lib)
        self.window.show()
        QAPP.processEvents()

    def tearDown(self):
        self.window.busy = False
        self.window.close()
        self.window.deleteLater()
        QAPP.processEvents()
        self.temp.cleanup()

    def test_list_search_category_and_pending_filter(self):
        self.assertEqual(self.window.table.rowCount(), 5)
        self.assertEqual(self.window.total_card.number.text(), '5')
        self.window.search.setText('储能')
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertIn('储能', self.window.detail_title.text())
        self.window.reset_filter()
        self.window.pending_filter()
        self.assertEqual(self.window.table.rowCount(), 1)
        self.window.reset_filter()
        self.window.category = ('人工智能', '大语言模型')
        self.window.refresh_list()
        self.assertEqual(self.window.table.rowCount(), 1)
        self.window.search.setText('不存在的内容')
        self.assertEqual(self.window.stack.currentIndex(), 1)
        self.assertFalse(self.window.open_button.isEnabled())

    def test_settings_save_and_cancel(self):
        settings = SettingsDialog(self.lib, self.window)
        self.assertEqual(settings.api_key.echoMode(), QLineEdit.EchoMode.Password)
        settings.model.setText('changed-model')
        settings.base_url.setText('https://another.invalid/v1')
        settings.save_and_use()
        self.assertEqual(settings.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(read_config(self.config)['OpenAI']['model'], 'changed-model')
        self.assertEqual(read_config(self.config)['OpenAI']['embedding_model'], 'preserved')
        self.assertTrue(self.config.with_suffix('.json.bak').exists())
        cancelled = SettingsDialog(self.lib, self.window)
        cancelled.model.setText('should-not-save')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            cancelled.reject()
        self.assertEqual(read_config(self.config)['OpenAI']['model'], 'changed-model')

    def test_connection_button_uses_unsaved_form_without_writing_file(self):
        dialog = SettingsDialog(self.lib, self.window)
        original = self.config.read_bytes()
        dialog.model.setText('unsaved-test-model')
        with patch('ui_settings.call_model_config', return_value={'major': 'A', 'minor': 'B', 'summary': 'ok', 'uncertain': False}) as model:
            dialog.test_connection()
            for _ in range(100):
                QTest.qWait(10)
                dialog.poll_test()
                if not dialog.testing:
                    break
            self.assertIn('连接成功', dialog.api_status.text())
            self.assertEqual(model.call_args.args[0]['OpenAI']['model'], 'unsaved-test-model')
        self.assertEqual(self.config.read_bytes(), original)
        dialog.dirty = False
        dialog.reject()

    def test_background_import_and_dedup(self):
        source = self.root / 'new.txt'
        source.write_text('Language model research\n\nAbstract: We study large language models.\n\nKeywords: large language model\n\n1 Introduction\nMotivation.', encoding='utf-8')
        self.window.import_paths([str(source), str(source)])
        for _ in range(200):
            QTest.qWait(10)
            self.window.poll()
            if not self.window.busy:
                break
        self.assertFalse(self.window.busy)
        self.assertEqual(len(self.lib.all()), 6)
        self.assertEqual(self.lib.all()[0]['minor'], '大语言模型')
        self.assertTrue(source.is_file())

    def test_render_main_and_settings_at_two_sizes(self):
        output = Path(__file__).resolve().parents[1] / 'output' / 'ui'
        output.mkdir(parents=True, exist_ok=True)
        self.window.status.setText('界面验证 · 当前为示例数据，不会写入正式文献库')
        self.window.resize(1510, 925)
        QAPP.processEvents()
        self.assertTrue(self.window.grab().save(str(output / 'interface.png')))
        self.window.resize(1200, 750)
        QAPP.processEvents()
        self.assertGreaterEqual(self.window.splitter.widget(0).width(), 465)
        self.assertGreaterEqual(self.window.splitter.widget(1).width(), 290)
        self.assertTrue(self.window.grab().save(str(output / 'interface-compact.png')))
        settings = SettingsDialog(self.lib, self.window)
        settings.show()
        QAPP.processEvents()
        self.assertTrue(settings.grab().save(str(output / 'settings.png')))
        settings.reject()

    def test_category_click_collapses_once_and_survives_refresh_and_reopen(self):
        tree = self.window.tree
        parent = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                      if tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)[0] == '人工智能')
        self.assertTrue(parent.isExpanded())
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=tree.visualItemRect(parent).center())
        self.assertFalse(parent.isExpanded())
        self.window.refresh()
        parent = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                      if tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)[0] == '人工智能')
        self.assertFalse(parent.isExpanded())
        self.window.save_layout()
        other = App(self.lib)
        other.show()
        QAPP.processEvents()
        parent = next(other.tree.topLevelItem(i) for i in range(other.tree.topLevelItemCount())
                      if other.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)[0] == '人工智能')
        self.assertFalse(parent.isExpanded())
        other.close()
        other.deleteLater()

    def test_directory_expand_select_actions_and_independent_search(self):
        self.window.search.setText('储能')
        self.window.set_page('catalog')
        page = self.window.catalog
        self.assertIn('5 篇', page.count.text())
        page.search.setText('大语言模型')
        self.assertIn('人工智能', page.tree.topLevelItem(0).text(0))
        major = page.tree.topLevelItem(0)
        self.assertTrue(major.isExpanded())
        minor = major.child(0)
        self.assertTrue(minor.isExpanded())
        paper = minor.child(0)
        page.tree.setCurrentItem(paper)
        self.assertEqual(self.window.selected_ids(), [paper.data(0, Qt.ItemDataRole.UserRole)[1]])
        with patch('app.open_paper') as opening:
            self.window.open_selected()
            self.assertEqual(opening.call_args.args[0], self.lib.get(self.window.selected_ids()[0])['path'])
        page.search.clear()
        page.expand_majors()
        page.collapse_all()
        self.assertTrue(all(not page.tree.topLevelItem(i).isExpanded() for i in range(page.tree.topLevelItemCount())))

    def test_large_library_pagination_and_incremental_directory(self):
        # Add metadata in one transaction: tests rendering bounds, not file I/O speed.
        with self.lib.connect() as db:
            db.executemany('INSERT INTO papers(path,title,major,minor,status) VALUES(?,?,?,?,?)',
                [(str(self.root / f'large-{i}.pdf'), f'文献 {i:04}', '大规模分类', '子类', '待处理') for i in range(205)])
        self.window.refresh()
        self.assertEqual(self.window.table.rowCount(), 50)
        first = self.window.table.item(0, 0).data(Qt.ItemDataRole.UserRole + 1)
        self.window.change_page(1)
        self.assertEqual(self.window.table.rowCount(), 50)
        self.assertNotEqual(first, self.window.table.item(0, 0).data(Qt.ItemDataRole.UserRole + 1))
        self.window.change_page(99)
        self.assertEqual(self.window.table.rowCount(), 10)
        self.assertFalse(self.window.next_button.isEnabled())
        self.window.search.setText('文献 0001')
        self.assertEqual(self.window.page_number, 0)
        self.assertEqual(self.window.table.rowCount(), 1)
        self.window.set_page('catalog')
        page = self.window.catalog
        major = next(page.tree.topLevelItem(i) for i in range(page.tree.topLevelItemCount()) if '大规模分类' in page.tree.topLevelItem(i).text(0))
        minor = major.child(0)
        self.assertEqual(minor.childCount(), 0)
        major.setExpanded(True)
        minor.setExpanded(True)
        self.assertEqual(minor.childCount(), 101)
        page.item_clicked(minor.child(100), 0)
        self.assertEqual(minor.childCount(), 201)
        page.item_clicked(minor.child(200), 0)
        self.assertEqual(minor.childCount(), 205)
        major.setExpanded(False)
        self.window.refresh()
        major = next(page.tree.topLevelItem(i) for i in range(page.tree.topLevelItemCount()) if '大规模分类' in page.tree.topLevelItem(i).text(0))
        self.assertFalse(major.isExpanded())

    def test_mouse_resize_and_layout_persistence(self):
        splitter = self.window.outer_splitter
        before = splitter.sizes()
        handle = splitter.handle(1)
        point = handle.rect().center()
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(handle, point + QPoint(55, 0))
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=point + QPoint(55, 0))
        QAPP.processEvents()
        self.assertNotEqual(before[0], splitter.sizes()[0])
        self.window.splitter.setSizes([500, 480])
        self.window.set_page('catalog')
        self.window.catalog.splitter.setSizes([540, 370])
        self.window.table.setColumnWidth(0, 375)
        self.window.catalog.tree.setColumnWidth(0, 460)
        QAPP.processEvents()
        self.window.save_layout()
        saved = self.lib.settings()['ui_state']
        self.assertEqual(saved['outer_sizes'], splitter.sizes())
        self.assertEqual(saved['page'], 'catalog')
        self.assertEqual(saved['overview_columns'][0], 375)
        other = App(self.lib)
        other.show()
        QAPP.processEvents()
        self.assertEqual(other.pages.currentWidget(), other.catalog)
        self.assertEqual(other.table.columnWidth(0), 375)
        # Folder actions remain visible; the name column now fills available space.
        self.assertEqual(other.catalog.tree.columnWidth(1), 210)
        self.assertLessEqual(other.catalog.tree.columnWidth(0) + 210, other.catalog.tree.viewport().width() + 2)
        self.assertAlmostEqual(other.outer_splitter.sizes()[0], saved['outer_sizes'][0], delta=8)
        other.close()
        other.deleteLater()

    def test_directory_render(self):
        output = Path(__file__).resolve().parents[1] / 'output' / 'ui'
        output.mkdir(parents=True, exist_ok=True)
        self.window.set_page('catalog')
        self.window.catalog.expand_majors()
        parent = self.window.catalog.tree.topLevelItem(0)
        parent.child(0).setExpanded(True)
        self.window.catalog.tree.setCurrentItem(parent.child(0).child(0))
        self.window.status.setText('界面验证 · 示例数据')
        QAPP.processEvents()
        self.assertTrue(self.window.grab().save(str(output / 'catalog.png')))
        self.window.resize(1200, 750)
        QAPP.processEvents()
        self.assertTrue(self.window.grab().save(str(output / 'catalog-compact.png')))

    def test_folder_row_buttons_dispatch_and_agent_only_on_request(self):
        self.window.set_page('catalog')
        page = self.window.catalog
        parent = page.tree.topLevelItem(0)
        key = tuple(parent.data(0, Qt.ItemDataRole.UserRole))[1:]
        controls = page.tree.itemWidget(parent, 1).findChildren(QPushButton)
        self.assertEqual([b.text() for b in controls], ['添加', '删除', '重命名'])
        with patch.object(self.window, 'delete_category_dialog') as delete:
            controls[1].click()
            delete.assert_called_once_with(key)
        with patch.object(self.window, 'rename_category_dialog') as rename:
            controls[2].click()
            rename.assert_called_once_with(key)
        with patch.object(self.window, 'create_category_dialog') as create:
            controls[0].click()
            create.assert_called_once_with(key[0])
        self.window.set_page('search')
        search = self.window.search_page
        search.mode.setCurrentIndex(1)
        with patch('agent_search.agent_search', return_value=([], '测试智能结果')) as agent:
            search.query.setText('电池')
            self.window.refresh()
            QTest.qWait(350)
            agent.assert_not_called()
            search.search()
            for _ in range(40):
                QTest.qWait(50)
                if not search.running:
                    break
            self.assertEqual(agent.call_count, 1)
            self.assertIn('测试智能结果', search.info.text())

    def drop_on(self, target, urls):
        mime = QMimeData()
        mime.setUrls(urls)
        actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
        enter = QDragEnterEvent(QPoint(10, 10), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(target, enter)
        self.assertTrue(enter.isAccepted())
        self.assertIn('松开鼠标', self.window.drop_hint.text())
        drop = QDropEvent(QPointF(10, 10), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        drop.setDropAction(Qt.DropAction.MoveAction)
        QApplication.sendEvent(target, drop)
        self.assertTrue(drop.isAccepted())
        self.assertEqual(drop.dropAction(), Qt.DropAction.CopyAction)
        for _ in range(200):
            QTest.qWait(10)
            self.window.poll()
            if not self.window.busy:
                break
        self.assertFalse(self.window.busy)

    def test_drop_multiple_files_into_search_does_not_paste_urls(self):
        paths = [self.root / '拖拽 文献.TXT', self.root / 'another.md']
        content = 'Language model paper\n\nAbstract: We study large language models.\n\nKeywords: large language model'
        for path in paths:
            path.write_text(content, encoding='utf-8')
        self.drop_on(self.window.search, [QUrl.fromLocalFile(str(p)) for p in paths])
        self.assertEqual(self.window.search.text(), '')
        self.assertEqual(len(self.lib.all()), 7)
        self.assertEqual(self.lib.all()[0]['minor'], '大语言模型')
        self.assertTrue(all(p.read_text(encoding='utf-8') == content for p in paths))
        self.drop_on(self.window, [QUrl.fromLocalFile(str(paths[0]))])
        self.assertEqual(len(self.lib.all()), 7)

    def test_drop_in_directory_accepts_only_supported_local_files(self):
        self.window.set_page('catalog')
        valid = self.root / 'valid.txt'
        valid.write_text('Title\n\nAbstract: A classification example.', encoding='utf-8')
        unsupported = self.root / 'ignore.exe'
        unsupported.touch()
        directory = self.root / 'folder.pdf'
        directory.mkdir()
        urls = [QUrl.fromLocalFile(str(p)) for p in (valid, unsupported, directory)] + [QUrl('https://example.invalid/paper.pdf')]
        self.drop_on(self.window.catalog.tree.viewport(), urls)
        self.assertEqual(len(self.lib.all()), 6)
        self.assertEqual(self.lib.all()[0]['path'], str(valid.resolve()))
        self.assertIn('已忽略 3', self.window.drop_hint.text())

    def test_drop_rejected_while_busy_or_without_supported_file(self):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.root / 'example-0.txt'))])
        event = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.window.busy = True
        QApplication.sendEvent(self.window, event)
        self.assertFalse(event.isAccepted())
        self.assertIn('正在整理', self.window.drop_hint.text())
        self.assertEqual(len(self.lib.all()), 5)
        self.window.busy = False
        mime.setUrls([QUrl('https://example.invalid/paper.pdf'), QUrl.fromLocalFile(str(self.root))])
        event = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.window, event)
        self.assertFalse(event.isAccepted())
        self.assertEqual(len(self.lib.all()), 5)

    def test_drag_leave_clears_hint_without_import(self):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.root / 'example-0.txt'))])
        enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.window, enter)
        self.assertIn('松开鼠标', self.window.drop_hint.text())
        QApplication.sendEvent(self.window, QDragLeaveEvent())
        self.assertNotIn('松开鼠标', self.window.drop_hint.text())
        self.assertEqual(len(self.lib.all()), 5)

    def wait_search(self):
        for _ in range(300):
            QTest.qWait(10)
            self.window.search_page.poll()
            if not self.window.search_page.running:
                break
        self.assertFalse(self.window.search_page.running)

    def test_new_search_page_notes_results_and_no_model_calls(self):
        from paper_links import parse_links
        self.lib.update(self.ids[-1], description='微电网能源消耗预测，适合能耗分析。',
            conclusion='【界面测试数据】仅在单个场景验证，跨域泛化仍待研究。',
            links=parse_links('补充资料 | https://example.invalid/paper'))
        self.window.refresh()
        self.window.set_page('search')
        page = self.window.search_page
        with patch('library_core.call_model_config', side_effect=AssertionError('Search must not call the model')):
            page.query.setText('我想找能耗预测的论文')
            page.search()
            self.wait_search()
        self.assertEqual(page.results[0]['paper']['id'], self.ids[-1])
        self.assertEqual(self.window.selected_ids(), [self.ids[-1]])
        with patch('app.open_paper') as opening:
            self.window.open_selected()
            self.assertEqual(opening.call_args.args[0], self.lib.get(self.ids[-1])['path'])
        output = Path(__file__).resolve().parents[1] / 'output' / 'ui'
        output.mkdir(parents=True, exist_ok=True)
        self.window.status.setText('界面验证 · 搜索仅使用示例数据')
        QAPP.processEvents()
        self.assertTrue(self.window.grab().save(str(output / 'search.png')))
        self.window.resize(1200, 750)
        QAPP.processEvents()
        self.assertTrue(self.window.grab().save(str(output / 'search-compact.png')))
        page.query.setText('量子拓扑绝缘体')
        page.search()
        self.wait_search()
        self.assertEqual(page.results, [])
        self.assertFalse(page.open_button.isEnabled())

    def test_empty_named_directory_and_internal_drop_preserve_source(self):
        from ui_drag import PAPER_MIME
        self.lib.create_category('我的项目', '待精读')
        self.window.refresh()
        tree = self.window.tree
        major = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)[0] == '我的项目')
        major.setExpanded(True)
        child = major.child(0)
        mime = QMimeData()
        mime.setData(PAPER_MIME, json.dumps(self.ids[:2]).encode())
        source_widget = self.window.table
        class InternalEnter(QDragEnterEvent):
            def source(self): return source_widget
        class InternalDrop(QDropEvent):
            def source(self): return source_widget
        QAPP.processEvents()
        point = tree.visualItemRect(child).center()
        enter = InternalEnter(point, Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(tree.viewport(), enter)
        self.assertTrue(enter.isAccepted())
        drop = InternalDrop(QPointF(point), Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(tree.viewport(), drop)
        self.assertTrue(drop.isAccepted())
        QAPP.processEvents()
        for paper_id in self.ids[:2]:
            paper = self.lib.get(paper_id)
            self.assertEqual((paper['major'], paper['minor']), ('我的项目', '待精读'))
            self.assertTrue(paper['category_locked'])
            self.assertTrue(Path(paper['path']).is_file())

    def test_user_can_edit_description_links_and_conclusion(self):
        paper_id = self.window.selected_ids()[0]
        def fill_dialog():
            dialog = QApplication.activeModalWidget()
            tabs = dialog.findChild(QTabWidget)
            by_label = {tabs.tabText(i): tabs.widget(i) for i in range(tabs.count())}
            by_label['个人描述'].setPlainText('用于微电网能耗预测的参考论文')
            by_label['结论'].setPlainText('只在一个数据集上验证。')
            by_label['关联链接'].setPlainText('补充数据 | https://example.invalid/dataset')
            save = next(b for b in dialog.findChildren(QPushButton) if b.text() == '保存修改')
            save.click()
        QTimer.singleShot(0, fill_dialog)
        self.window.edit_dialog()
        paper = self.lib.get(paper_id)
        self.assertIn('微电网', paper['description'])
        self.assertEqual(paper['conclusion'], '只在一个数据集上验证。')
        self.assertEqual(json.loads(paper['links'])[0]['label'], '补充数据')
        self.assertEqual(paper['end_checked'], 1)

    def test_old_paper_conclusion_backfill_without_model_or_description_loss(self):
        paper_id = self.ids[-1]
        paper = self.lib.get(paper_id)
        Path(paper['path']).write_text('Title\n\n5 Conclusions\nOnly one dataset was used.\n\nReferences\nNo.', encoding='utf-8')
        self.lib.update(paper_id, description='保留的个人描述')
        with patch('app.organize', side_effect=AssertionError('Backfill must not call model')):
            self.window.process([paper_id], end_only=True)
            for _ in range(200):
                QTest.qWait(10)
                self.window.poll()
                if not self.window.busy:
                    break
        self.assertFalse(self.window.busy)
        paper = self.lib.get(paper_id)
        self.assertIn('one dataset', paper['conclusion'])
        self.assertEqual(paper['description'], '保留的个人描述')
        self.assertEqual(paper['status'], '模型已整理')


if __name__ == '__main__':
    unittest.main()
