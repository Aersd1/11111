import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from library_core import Library, DEFAULT_SETTINGS, extract, organize, rule_classify, open_paper, call_model

SAMPLE = '''A Study of Large Language Models

Abstract: We study large language models for document classification. A small test compares two prompts.

Keywords: large language model; document classification

1 Introduction
First introduction paragraph describes the motivation.

Second introduction paragraph states the scope.

Third paragraph must never be sent.

2 Methods
The full method must never be sent.
'''


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'paper.txt'
        self.source.write_text(SAMPLE, encoding='utf-8')
        self.lib = Library(self.root / 'index.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_dedup_persistence_and_removal_preserve_source(self):
        paper_id, created = self.lib.add(self.source)
        self.assertTrue(created)
        self.assertEqual(self.lib.add(self.source), (paper_id, False))
        self.lib.update(paper_id, major='AI', minor='LLM', summary='Example')
        self.assertEqual(Library(self.lib.path).get(paper_id)['minor'], 'LLM')
        self.lib.remove(paper_id)
        self.assertEqual(self.lib.all(), [])
        self.assertEqual(self.source.read_text(encoding='utf-8'), SAMPLE)

    def test_extract_scope(self):
        paper = extract(self.source)
        self.assertEqual(paper['title'], 'A Study of Large Language Models')
        self.assertNotIn('Introduction', paper['abstract'])
        self.assertIn('document classification', paper['keywords'])
        self.assertIn('Second introduction', paper['introduction'])
        self.assertNotIn('Third paragraph', paper['introduction'])
        self.assertNotIn('full method', json.dumps(paper))

    def test_chinese_headings(self):
        self.source.write_text('基于深度学习的图像分割\n\n摘要：本文研究图像分割。\n\n关键词：图像分割；深度学习\n\n1 引言\n第一段。\n\n第二段。\n\n第三段。\n\n2 方法\n不能发送。', encoding='utf-8')
        paper = extract(self.source)
        self.assertEqual(paper['abstract'], '本文研究图像分割。')
        self.assertEqual(paper['introduction'], '第一段。\n\n第二段。')

    def test_uncertain_model_receives_two_intro_paragraphs_in_single_request(self):
        calls = []
        def fake(config, payload):
            calls.append(copy.deepcopy(payload))
            return {'major': '人工智能', 'minor': '语言模型', 'summary': '研究提示词，摘要未提供数值结果。', 'uncertain': len(calls) == 1}
        result = organize(extract(self.source), DEFAULT_SETTINGS, [], model_fn=fake)
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(calls[0]), {'title', 'title_evidence', 'abstract', 'keywords', 'existing_categories', 'introduction_first_two_paragraphs', 'conclusion', 'limitations'})
        self.assertIn('Second introduction', calls[0]['introduction_first_two_paragraphs'])
        self.assertNotIn('Third paragraph', json.dumps(calls))
        self.assertEqual(result['major'], '待分类')

    def test_confident_model_no_second_request(self):
        calls = []
        def fake(config, payload):
            calls.append(payload)
            return {'major': 'AI', 'minor': 'LLM', 'summary': 'Summary', 'uncertain': False}
        organize(extract(self.source), DEFAULT_SETTINGS, [], model_fn=fake)
        self.assertEqual(len(calls), 1)
        self.assertIn('introduction_first_two_paragraphs', calls[0])

    def test_still_uncertain_is_pending(self):
        def fake(*args):
            return {'major': 'Guess', 'minor': 'Guess', 'summary': 'Insufficient', 'uncertain': True}
        result = organize(extract(self.source), DEFAULT_SETTINGS, [], model_fn=fake)
        self.assertEqual((result['major'], result['minor']), ('待分类', '待确认'))

    def test_rules_and_ties(self):
        paper = extract(self.source)
        result = rule_classify(paper, DEFAULT_SETTINGS['rules'])
        self.assertEqual(result['minor'], '大语言模型')
        self.assertIn('摘要摘录', result['summary'])
        rules = [{'major': 'A', 'minor': 'B', 'terms': ['large language']}, {'major': 'C', 'minor': 'D', 'terms': ['large language']}]
        self.assertEqual(rule_classify(paper, rules)['major'], '待分类')

    def test_settings_persist(self):
        value = {**DEFAULT_SETTINGS, 'opener': '浏览器', 'mode': '手动'}
        self.lib.save_settings(value)
        self.assertEqual(Library(self.lib.path).settings(), value)

    def test_pdf_front_pages_only(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        from library_core import read_front
        writer = PdfWriter()
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        for number in range(1, 6):
            page = writer.add_blank_page(width=600, height=800)
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(f'BT /F1 12 Tf 50 750 Td (PAGE{number}) Tj ET'.encode())
            page[NameObject('/Contents')] = writer._add_object(stream)
        pdf = self.root / 'front.pdf'
        with pdf.open('wb') as output:
            writer.write(output)
        text, _ = read_front(pdf)
        self.assertIn('PAGE4', text)
        self.assertNotIn('PAGE5', text)

    def test_manually_entered_introduction_is_limited(self):
        paper = extract(self.source)
        paper['introduction'] = 'First\n\nSecond\n\nThird must not be sent'
        calls = []
        def fake(config, payload):
            calls.append(copy.deepcopy(payload))
            return {'major': 'A', 'minor': 'B', 'summary': 'summary', 'uncertain': True}
        organize(paper, DEFAULT_SETTINGS, [], model_fn=fake)
        self.assertEqual(calls[0]['introduction_first_two_paragraphs'], 'First\n\nSecond')

    def test_open_custom_uses_argument_array_and_missing_file_reports(self):
        exe = self.root / 'reader with spaces.exe'
        exe.touch()
        exe.chmod(0o700)
        settings = {**DEFAULT_SETTINGS, 'opener': '指定程序', 'program': str(exe)}
        with patch('library_core.subprocess.Popen') as process:
            open_paper(self.source, settings)
            process.assert_called_once_with([str(exe), str(self.source)])
        with self.assertRaises(ValueError):
            open_paper(self.root / 'missing.pdf', settings)

    def test_invalid_model_response_and_secret_safe_error(self):
        config = self.root / 'api.json'
        config.write_text(json.dumps({'OpenAI': {'base_url': 'https://example.invalid/v1', 'api_key': 'SECRET-TEST', 'model': 'test'}}))
        with patch('library_core.urllib.request.urlopen', side_effect=OSError('SECRET-TEST')):
            with self.assertRaises(RuntimeError) as caught:
                call_model(config, {})
            self.assertNotIn('SECRET-TEST', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
