import json
import tempfile
import threading
import unittest
from pathlib import Path
from document_markdown import translate_document, file_digest
from pdf_translation import read_page_translations
from model_response import ModelResponseError


class PdfTranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / 'api.json'
        self.config.write_text('{}', 'utf-8')
        (self.root / 'document.json').write_text(json.dumps({'source_name': 'paper.pdf'}), 'utf-8')
        (self.root / '原文.md').write_text('\n\n'.join(f'## 第 {i} 页\n\nText from page {i}. $x_i$\n\n![原貌](assets/page-{i}-original.png)' for i in range(1, 12)), 'utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_parallel_page_batches_and_edited_page_mapping(self):
        gate = threading.Barrier(2)
        seen, updates = [], []
        def caller(c, instruction, payload, maximum):
            seen.append(payload)
            if payload['pages'][0]['page'] <= 10:
                gate.wait(timeout=5)
            return {'pages': [{'page': p['page'], 'translation': '中文 ' + p['text']} for p in payload['pages']]}, 0
        result = translate_document(self.root, {'api_config': str(self.config)}, caller=caller, on_batch=updates.append)
        self.assertEqual(result['calls'], 3)
        self.assertEqual(sorted(len(p['pages']) for p in seen), [1, 5, 5])
        self.assertEqual(len(updates), 3)
        self.assertNotIn('original.png', json.dumps(seen))
        pages = read_page_translations(self.root)
        self.assertEqual(sorted(pages), list(range(1, 12)))
        self.assertIn('page 7.', pages[7])
        self.assertNotIn('page 6.', pages[7])
        self.assertIn('$x_i$', pages[7])
        self.assertTrue(translate_document(self.root, {'api_config': str(self.config)}, caller=caller)['cached'])
        target = self.root / '中文.md'
        target.write_text(target.read_text('utf-8').replace('page 7.', '人工校订第七页。'), 'utf-8')
        self.assertIn('人工校订第七页。', read_page_translations(self.root)[7])

    def test_wrong_page_numbers_rejected_and_successful_peer_saved(self):
        def caller(c, i, payload, m):
            ids = [p['page'] for p in payload['pages']]
            if ids[0] == 1:
                return {'pages': [{'page': 999, 'translation': 'wrong'}]}, 0
            return {'pages': [{'page': p['page'], 'translation': p['text']} for p in payload['pages']]}, 0
        with self.assertRaises(ModelResponseError):
            translate_document(self.root, {'api_config': str(self.config)}, caller=caller, wait=lambda s: None)
        pages = read_page_translations(self.root)
        self.assertTrue(set(range(6, 11)).issubset(pages))
        self.assertNotIn(1, pages)
        self.assertNotIn(999, pages)
        called = []
        def success(c,i,p,m):
            called.extend(x['page'] for x in p['pages'])
            return {'pages': [{'page': x['page'], 'translation': x['text']} for x in p['pages']]},0
        translate_document(self.root, {'api_config': str(self.config)}, caller=success)
        self.assertFalse(set(range(6, 11)).intersection(called))


class PdfReaderTests(unittest.TestCase):
    def test_native_pdf_page_is_identical_to_chinese_page(self):
        from test_live_markdown import QAPP, QTest, wait_render, javascript
        from reader_ui import DocumentReader
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'paper.pdf'
            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=400, height=600)
            writer.write(str(source))
            reader = DocumentReader({'id': 1, 'path': str(source), 'title': '页码对应测试', 'summary': ''}, {}, root/'cache')
            reader.show()
            for _ in range(200):
                if not reader.busy:
                    break
                QTest.qWait(30)
            try:
                self.assertFalse(reader.busy)
                self.assertEqual(reader.pdf.document.pageCount(), 3)
                self.assertFalse(reader.source.isVisible())
                self.assertTrue(reader.pdf.isVisible())
                self.assertFalse(reader.scroll_timer.isActive())
                text = '\n\n'.join(f'## 第 {n} 页\n\n中文内容{n}' for n in range(1,4))
                (reader.folder/'中文.md').write_text(text, 'utf-8')
                (reader.folder/'中文分页.json').write_text(json.dumps({'source':file_digest(reader.folder/'原文.md'),'complete':True,'pages':{'1':'','2':'','3':''}}), 'utf-8')
                reader.load_views()
                from unittest.mock import patch
                exported = root/'保存的中文.md'
                with patch('reader_ui.QFileDialog.getSaveFileName', return_value=(str(exported), 'Markdown (*.md)')):
                    reader.save_chinese()
                self.assertIn('中文内容3', exported.read_text('utf-8'))
                reader.pdf.view.verticalScrollBar().setValue(reader.pdf.view.verticalScrollBar().maximum())
                QTest.qWait(150)
                self.assertEqual(reader.page_number.value(), 3)
                wait_render(reader.chinese, 'document.body.innerText.includes("中文内容3")')
                self.assertEqual(reader.pdf.view.pageNavigator().currentPage(), 2)
                self.assertNotIn('中文内容2', reader.chinese._html)
                self.assertNotIn('original.png', reader.chinese._html)
                reader.pdf.add_note()
                self.assertEqual(reader.pdf.notes[0]['page'], 3)
                reader.pdf.widgets[0].editor.setPlainText('PDF 原文批注')
                from PySide6.QtCore import Qt, QPoint
                sticky = reader.pdf.widgets[0]
                previous = sticky.pos()
                QTest.mousePress(sticky, Qt.MouseButton.LeftButton, pos=QPoint(12,12))
                QTest.mouseMove(sticky, QPoint(-48,42), 100)
                QTest.mouseRelease(sticky, Qt.MouseButton.LeftButton, pos=QPoint(12,12))
                self.assertNotEqual(sticky.pos(), previous)
                wait_render(reader.chinese, '!!window.noteBridge')
                reader.chinese.add_note()
                wait_render(reader.chinese, 'document.querySelectorAll(".paper-note").length === 1')
                javascript(reader.chinese, 'noteItems[0].text="关闭前最后一条批注";document.querySelector("textarea").value=noteItems[0].text;')
                # Close immediately without waiting for the ordinary bridge autosave.
                folder = reader.folder
                reader.close()
                QTest.qWait(350)
                saved = json.loads((folder/'批注.json').read_text('utf-8'))
                self.assertEqual(saved['PDF原文'][0]['text'], 'PDF 原文批注')
                self.assertEqual(saved['中文译文'][0]['text'], '关闭前最后一条批注')
                reader = DocumentReader({'id':1,'path':str(source),'title':'重新打开','summary':''}, {}, root/'cache')
                reader.show()
                for _ in range(200):
                    if not reader.busy: break
                    QTest.qWait(30)
                self.assertEqual(reader.pdf.notes[0]['text'], 'PDF 原文批注')
                self.assertEqual(reader.chinese.notes[0]['text'], '关闭前最后一条批注')
                self.assertIn('中文内容3', (reader.folder/'中文.md').read_text('utf-8'))
                reader.page_number.setValue(1)
                self.assertFalse(reader.pdf.widgets)
                self.assertEqual(reader.pdf.view.pageNavigator().currentPage(), 0)
            finally:
                reader.close()
                QTest.qWait(150)
