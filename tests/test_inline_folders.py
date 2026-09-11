import os
from pathlib import Path
import tempfile
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from app import App
from library_core import Library
from PySide6.QtCore import Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ui_theme import setup_application

QAPP = QApplication.instance() or QApplication([])
setup_application(QAPP)


class InlineFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.lib = Library(Path(self.temp.name) / 'library.sqlite3')
        self.window = App(self.lib)
        self.window.show()
        QAPP.processEvents()

    def tearDown(self):
        self.window.catalog.cancel_folder_edit()
        self.window.close()
        self.window.deleteLater()
        QAPP.processEvents()
        self.temp.cleanup()

    def enter_name(self, name):
        editor = self.window.catalog.folder_edit['editor']
        editor.selectAll()
        event = QInputMethodEvent()
        event.setCommitString(name)
        QAPP.sendEvent(editor, event)
        QTest.keyClick(editor, Qt.Key.Key_Return)
        QAPP.processEvents()

    def test_major_minor_and_rename_are_inline_and_create_real_directories(self):
        self.window.create_category_dialog()
        self.assertIsNone(QApplication.activeModalWidget())
        self.assertEqual(self.window.pages.currentWidget(), self.window.catalog)
        self.enter_name('一次输入完整的大类名称')
        self.assertTrue((self.lib.catalog_root / '一次输入完整的大类名称').is_dir())
        self.window.create_category_dialog('一次输入完整的大类名称')
        item = self.window.catalog.folder_edit['item']
        self.assertIsNotNone(item.parent())
        self.enter_name('摘要与局限分析')
        self.assertTrue((self.lib.catalog_root / '一次输入完整的大类名称/摘要与局限分析').is_dir())
        self.window.rename_category_dialog(('一次输入完整的大类名称', '摘要与局限分析'))
        self.enter_name('重命名成功')
        self.assertTrue((self.lib.catalog_root / '一次输入完整的大类名称/重命名成功').is_dir())
        self.assertFalse((self.lib.catalog_root / '一次输入完整的大类名称/摘要与局限分析').exists())

    def test_composition_and_text_survive_refresh_escape_creates_nothing(self):
        self.window.create_category_dialog()
        editor = self.window.catalog.folder_edit['editor']
        editor.setText('正在输入的名称')
        QAPP.sendEvent(editor, QInputMethodEvent('拼音候选', []))
        self.window.refresh()
        self.assertIs(self.window.catalog.folder_edit['editor'], editor)
        self.assertEqual(editor.text(), '正在输入的名称')
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        QAPP.processEvents()
        self.assertEqual(self.lib.categories(), [])
        self.assertIsNone(self.window.catalog.folder_edit)

    def test_duplicate_and_invalid_names_keep_editor_without_dialog(self):
        self.lib.create_category('已有目录')
        self.window.create_category_dialog()
        self.enter_name('已有目录')
        self.assertIsNotNone(self.window.catalog.folder_edit)
        self.assertIn('已存在', self.window.catalog.folder_hint.text())
        self.assertIsNone(QApplication.activeModalWidget())
        self.enter_name('非法/名称')
        self.assertIsNotNone(self.window.catalog.folder_edit)
        self.enter_name('更正后的名称')
        self.assertIsNone(self.window.catalog.folder_edit)
        self.assertIn(('更正后的名称', ''), self.lib.categories())

    def test_focus_out_saves_and_inline_render(self):
        self.window.create_category_dialog()
        editor = self.window.catalog.folder_edit['editor']
        editor.setText('直接在文件夹位置输入')
        output = Path(__file__).resolve().parents[1] / 'output/ui'
        output.mkdir(parents=True, exist_ok=True)
        QAPP.processEvents()
        self.window.grab().save(str(output / 'inline-folder.png'))
        self.window.catalog.search.setFocus()
        QAPP.processEvents()
        self.assertIn(('直接在文件夹位置输入', ''), self.lib.categories())


if __name__ == '__main__':
    unittest.main()
