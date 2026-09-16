import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from library_core import Library
from document_markdown import prepare_document, translate_document, translation_chunks, translation_current
from paper_names import rename_imported, title_filename
from PySide6.QtWidgets import QApplication

APP = QApplication.instance() or QApplication([])


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / 'api.json'
        self.config.write_text('{}', 'utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_pdf_text_and_vector_page_image_are_preserved(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=400)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        content = DecodedStreamObject()
        content.set_data(b'BT /F1 12 Tf 30 350 Td (A complete paper.) Tj ET 1 0 0 rg 40 40 120 100 re f')
        page[NameObject('/Contents')] = writer._add_object(content)
        source = self.root / 'paper.pdf'
        writer.write(str(source))
        folder = prepare_document(source, self.root / 'documents')
        markdown = (folder / '原文.md').read_text('utf-8')
        self.assertIn('A complete paper.', markdown)
        self.assertIn('assets/page-1-original.png', markdown)
        self.assertGreater((folder / 'assets/page-1-original.png').stat().st_size, 100)
        with patch('pypdf.PdfReader', side_effect=AssertionError('must use local bundle')):
            self.assertEqual(prepare_document(source, self.root / 'documents'), folder)

    def test_docx_text_and_embedded_picture_are_preserved(self):
        import io
        from PIL import Image
        data = io.BytesIO()
        Image.new('RGB', (15, 15), 'red').save(data, format='PNG')
        source = self.root / 'paper.docx'
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:r><w:t>Paragraph with image</w:t><w:drawing><a:blip r:embed="image1"/></w:drawing></w:r></w:p></w:body></w:document>')
            archive.writestr('word/_rels/document.xml.rels', '<Relationships><Relationship Id="image1" Target="media/image.png"/></Relationships>')
            archive.writestr('word/media/image.png', data.getvalue())
        folder = prepare_document(source, self.root / 'documents')
        self.assertIn('Paragraph with image', (folder / '原文.md').read_text('utf-8'))
        self.assertTrue((folder / 'assets/docx-1.png').exists())

    def test_page_batches_join_sentences_without_losing_source(self):
        from document_markdown import translation_batches
        original = ''.join(f'## 第 {i} 页\n\nPage {i} finished.\n\n' for i in range(1, 5))
        original += '## 第 5 页\n\nThe result depends on\n\n![image](assets/p5.png)\n\n## 第 6 页\n\nthe sample size. Next sentence.\n\n'
        original += ''.join(f'## 第 {i} 页\n\nPage {i} finished.\n\n' for i in range(7, 12))
        batches = translation_batches(original, 5)
        self.assertEqual(len(batches), 3)
        self.assertIn('the sample size.', batches[0]['text'])
        self.assertNotIn('the sample size.', batches[1]['text'])
        self.assertIn('Next sentence.', batches[1]['text'])
        restored = ''
        for b in batches:
            text = b['text']
            for marker, value in b['markers'].items(): text = text.replace(marker, value)
            restored += text
        self.assertEqual(restored, original)
        self.assertEqual(len(translation_batches(original, 3)), 4)

    def test_incremental_pages_retry_only_failed_batch_and_resume(self):
        from document_markdown import partial_translation
        from model_response import ModelResponseError
        source = self.root / 'pages.md'
        source.write_text(''.join(f'## 第 {i} 页\n\nPage {i}.\n\n' for i in range(1, 12)), 'utf-8')
        folder = prepare_document(source, self.root / 'documents')
        calls, shown, waits = [], [], []
        def caller(c, i, payload, maximum):
            text = payload['source_markdown']
            calls.append(text)
            if '第 6 页' in text:
                self.assertTrue((folder / '中文.翻译中.md').is_file())
                self.assertEqual(shown, [1])
                raise ModelResponseError('connection cut', retryable=True)
            return {'translation': '中文 ' + text}, 0
        with self.assertRaises(ModelResponseError):
            translate_document(folder, {'api_config': str(self.config)}, caller=caller,
                on_batch=lambda info: shown.append(info['completed']), wait=waits.append)
        self.assertEqual(len(calls), 4)
        self.assertEqual(waits, [2,4])
        self.assertEqual(partial_translation(folder)['completed'], 1)
        self.assertFalse(translation_current(folder))
        result = translate_document(folder, {'api_config': str(self.config)},
            caller=lambda c,i,p,m: ({'translation':'中文 ' + p['source_markdown']},0))
        self.assertEqual(result['calls'], 2)
        self.assertTrue(translation_current(folder))
        self.assertIsNone(partial_translation(folder))

    def test_translation_preserves_math_images_and_reuses_complete_result(self):
        source = self.root / 'paper.md'
        source.write_text('# Heading\n\nText $x_i^2$ and $$\\frac{a}{b}$$\n\n![figure](figure.png)', 'utf-8')
        from PIL import Image
        Image.new('RGB', (5, 5)).save(self.root / 'figure.png')
        folder = prepare_document(source, self.root / 'documents')
        calls = []
        def caller(config, instruction, payload, maximum):
            calls.append(payload)
            return {'translation': payload['source_markdown'].replace('Heading', '标题').replace('Text', '正文')}, 10
        result = translate_document(folder, {'api_config': str(self.config)}, caller=caller)
        self.assertEqual(result['calls'], 1)
        zh = (folder / '中文.md').read_text('utf-8')
        self.assertIn('正文 $x_i^2$', zh)
        self.assertIn('$$\\frac{a}{b}$$', zh)
        self.assertIn('assets/1.png', zh)
        self.assertTrue(translation_current(folder))
        self.assertEqual(translate_document(folder, {'api_config': str(self.config)}, caller=caller)['calls'], 0)
        self.assertEqual(len(calls), 1)
        self.assertTrue((folder / '中英对照.md').exists())

    def test_translation_failure_keeps_existing_output_and_resumes(self):
        source = self.root / 'paper.md'
        source.write_text(''.join(f'Paragraph {i}\n' + 'paragraph words ' * 180 + '\n\n' for i in range(4)), 'utf-8')
        folder = prepare_document(source, self.root / 'documents')
        (folder / '中文.md').write_text('old complete translation', 'utf-8')
        sent = []
        def failing(config, instruction, payload, maximum):
            sent.append(payload['source_markdown'])
            if len(sent) == 2:
                raise RuntimeError('connection interrupted')
            return {'translation': '中文 ' + payload['source_markdown']}, 0
        with self.assertRaises(RuntimeError):
            translate_document(folder, {'api_config': str(self.config)}, caller=failing)
        self.assertEqual((folder / '中文.md').read_text('utf-8'), 'old complete translation')
        count = len(translation_chunks((folder / '原文.md').read_text('utf-8')))
        result = translate_document(folder, {'api_config': str(self.config)}, caller=lambda c,i,p,m: ({'translation':'中文 ' + p['source_markdown']},0))
        self.assertEqual(result['calls'], count - 1)
        self.assertTrue(translation_current(folder))

    def test_missing_formula_marker_is_rejected(self):
        source = self.root / 'paper.md'
        source.write_text('Test $a+b$', 'utf-8')
        folder = prepare_document(source, self.root / 'documents')
        with self.assertRaisesRegex(ValueError, '丢失'):
            translate_document(folder, {'api_config': str(self.config)}, caller=lambda *a: ({'translation':'Missing formula'},0))
        self.assertFalse((folder / '中文.md').exists())

    def test_filename_collision_missing_title_and_rollback(self):
        lib = Library(self.root / 'library.sqlite3')
        source = self.root / 'old.pdf'
        source.write_bytes(b'original')
        ident, _ = lib.add(source)
        self.assertIsNone(rename_imported(lib, ident, 'Preprint'))
        occupied = self.root / 'A real title.pdf'
        occupied.write_bytes(b'existing')
        result = rename_imported(lib, ident, 'A real title')
        self.assertEqual(result.name, 'A real title (2).pdf')
        self.assertEqual(occupied.read_bytes(), b'existing')
        self.assertFalse(source.exists())
        self.assertEqual(Path(lib.get(ident)['path']), result)
        with patch.object(lib, 'update', side_effect=OSError('cannot save')):
            with self.assertRaises(OSError):
                rename_imported(lib, ident, 'Different title')
        self.assertTrue(result.exists())
        self.assertFalse((self.root / 'Different title.pdf').exists())
        self.assertNotIn('/', title_filename('Title: A / B?'))
