import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from library_core import Library, DEFAULT_SETTINGS, organize, extract
from paper_sections import extract_end, parse_end_sections
from local_search import BM25Index
from paper_links import parse_links, decode_links, open_link


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lib = Library(self.root / 'library.sqlite3')
        self.source = self.root / 'paper.txt'
        self.source.write_text('A paper\n\nAbstract: We classify documents.\n\nKeywords: classification\n\n1 Introduction\nMotivation.\n\n2 Methods\nFULL METHOD NOT SENT\n\n5 Conclusions\nThe study supports document classification.\n\n6 Limitations\nOnly one dataset was evaluated.\n\nReferences\nREFERENCE NOT SENT', encoding='utf-8')
        self.paper_id, _ = self.lib.add(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def test_conclusion_limitations_extraction_and_model_payload(self):
        fields = extract(self.source)
        fields.update(description='PRIVATE NOTE', links='PRIVATE LINK')
        self.assertIn('supports document', fields['conclusion'])
        self.assertIn('one dataset', fields['limitations'])
        self.assertNotIn('REFERENCE', fields['conclusion'] + fields['limitations'])
        sent = []
        def model(config, payload):
            sent.append(payload)
            return {'major': 'AI', 'minor': '分类', 'summary': '局限：只使用一个数据集。', 'uncertain': False}
        result = organize(fields, DEFAULT_SETTINGS, [], model_fn=model)
        self.assertEqual(len(sent), 1)
        self.assertIn('conclusion', sent[0])
        self.assertIn('limitations', sent[0])
        self.assertIn('Motivation.', sent[0]['introduction_first_two_paragraphs'])
        self.assertNotIn('FULL METHOD', json.dumps(sent))
        self.assertNotIn('PRIVATE', json.dumps(sent))
        self.assertIn('结论', result['basis'])

    def test_chinese_sections_and_missing_section_are_not_invented(self):
        result = parse_end_sections('正文\n\n5 结论与展望\n本文得到主要结论。\n\n6 研究局限性\n样本较少。\n\n参考文献\n不应提取。')
        self.assertEqual(result['conclusion'], '本文得到主要结论。')
        self.assertEqual(result['limitations'], '样本较少。')
        self.assertEqual(parse_end_sections('Conclusions drawn from the data are discussed here.'), {'conclusion': '', 'limitations': ''})

    def test_pdf_end_read_is_bounded(self):
        from paper_sections import end_text
        touched = []
        class Page:
            def __init__(self, number): self.number = number
            def extract_text(self):
                touched.append(self.number)
                return '5 Conclusions\nConclusion body.' if self.number == 25 else ''
        class Reader:
            pages = [Page(i) for i in range(30)]
        with patch('pypdf.PdfReader', return_value=Reader()):
            end_text(self.root / 'fake.pdf')
        self.assertEqual(touched, list(range(18, 30)))

    def test_pdf_import_reads_each_page_once_and_finds_middle_conclusion(self):
        from types import SimpleNamespace
        touched = []
        texts = ['body'] * 30
        texts[0] = 'A paper\n\nAbstract: Test.\n\n1 Introduction\nFirst.\n\nSecond.\n\n2 Methods\nmethod'
        texts[9] = '5 Conclusions\nMain result.\n\n6 Limitations\nSmall sample.\n\nReferences\nCitations.'
        def read(index):
            touched.append(index)
            return texts[index]
        pages = [SimpleNamespace(extract_text=lambda i=i: read(i)) for i in range(30)]
        with patch('pypdf.PdfReader', return_value=SimpleNamespace(pages=pages, metadata=None)) as reader:
            fields = extract(self.root / 'fake.pdf')
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(len(touched), len(set(touched)))
        self.assertEqual(fields['conclusion'], 'Main result.')
        self.assertEqual(fields['limitations'], 'Small sample.')

    def test_short_pdf_overlapping_front_and_tail_are_not_reparsed(self):
        from types import SimpleNamespace
        touched = []
        def read(i):
            touched.append(i)
            return '6 Conclusions\nResult.\n\n7 Limitations\nLimited.' if i == 4 else 'A paper'
        pages = [SimpleNamespace(extract_text=lambda i=i: read(i)) for i in range(5)]
        with patch('pypdf.PdfReader', return_value=SimpleNamespace(pages=pages, metadata=None)):
            fields = extract(self.root / 'fake.pdf')
        self.assertEqual(touched, list(range(5)))
        self.assertEqual(fields['limitations'], 'Limited.')

    def test_manual_category_and_user_description_survive_organizing(self):
        self.lib.update(self.paper_id, description='我的笔记', links=parse_links('原文 | https://example.invalid/paper'))
        self.lib.create_category('自建大类', '我的小类')
        self.lib.assign_category([self.paper_id], '自建大类', '我的小类')
        paper = self.lib.get(self.paper_id)
        paper.update(extract(self.source))
        result = organize(paper, DEFAULT_SETTINGS, [], model_fn=lambda *_: {'major': '模型类别', 'minor': '模型小类', 'summary': '总结', 'uncertain': False})
        self.lib.update(self.paper_id, **result)
        saved = self.lib.get(self.paper_id)
        self.assertEqual((saved['major'], saved['minor']), ('自建大类', '我的小类'))
        self.assertEqual(saved['description'], '我的笔记')
        self.assertEqual(decode_links(saved['links'])[0]['label'], '原文')
        self.assertTrue(self.source.is_file())

    def test_empty_categories_rename_and_atomic_assignment(self):
        self.lib.create_category('空大类')
        self.assertIn(('空大类', ''), Library(self.lib.path).categories())
        self.lib.assign_category([self.paper_id], '空大类')
        self.assertEqual(self.lib.get(self.paper_id)['minor'], '未细分')
        self.lib.rename_category('空大类', '', '重命名大类')
        self.assertEqual(self.lib.get(self.paper_id)['major'], '重命名大类')
        with self.assertRaises(ValueError):
            self.lib.assign_category([self.paper_id, 9999], '错误归类', '错误')
        self.assertEqual(self.lib.get(self.paper_id)['major'], '重命名大类')
        self.assertNotIn(('错误归类', '错误'), self.lib.categories())

    def test_bm25_natural_query_cross_language_notes_and_no_match(self):
        self.lib.update(self.paper_id, title='Microgrid forecasting', description='Energy consumption prediction for microgrids', conclusion='Validated on only one building.')
        index = BM25Index()
        index.update(self.lib.all())
        results = index.search('我想找能源消耗预测相关的论文')
        self.assertEqual(results[0]['paper']['id'], self.paper_id)
        self.assertEqual(results[0]['evidence'][0]['field'], '个人描述')
        self.assertEqual(index.search('量子拓扑绝缘体'), [])
        self.lib.update(self.paper_id, description='生物蛋白质折叠', title='生物分析')
        index.update(self.lib.all())
        self.assertEqual(index.search('能源消耗预测'), [])
        self.assertTrue(index.search('蛋白质'))
        self.lib.remove(self.paper_id)
        index.update(self.lib.all())
        self.assertEqual(index.search('蛋白质'), [])

    def test_links_validation_and_opening(self):
        value = parse_links('作者主页 | https://example.invalid\n附件 | ' + str(self.source))
        self.assertEqual(len(decode_links(value)), 2)
        with self.assertRaises(ValueError):
            parse_links('危险 | javascript:alert(1)')
        with patch('paper_links.os.startfile') as opening:
            open_link(str(self.source))
            opening.assert_called_once_with(str(self.source))

    def test_limitations_field_is_searchable_and_payload_is_bounded(self):
        self.lib.update(self.paper_id, limitations='Only one dataset was evaluated.')
        index = BM25Index()
        index.update(self.lib.all())
        self.assertTrue(index.search('局限性'))
        paper = self.lib.get(self.paper_id)
        paper.update(abstract='a' * 10000, conclusion='c' * 10000, limitations='l' * 10000)
        sent = []
        def model(config, payload):
            sent.append(payload)
            return {'major': 'A', 'minor': 'B', 'summary': 'Limited content', 'uncertain': False}
        organize(paper, DEFAULT_SETTINGS, [], model_fn=model)
        self.assertEqual(len(sent[0]['abstract']), 6000)
        self.assertEqual(len(sent[0]['conclusion']), 5000)
        self.assertEqual(len(sent[0]['limitations']), 2500)

    def test_migration_preserves_old_rows(self):
        old = self.root / 'old.sqlite3'
        db = sqlite3.connect(old)
        db.execute("CREATE TABLE papers(id INTEGER PRIMARY KEY,path TEXT UNIQUE,title TEXT,abstract TEXT DEFAULT '',keywords TEXT DEFAULT '',introduction TEXT DEFAULT '',major TEXT DEFAULT '待分类',minor TEXT DEFAULT '待确认',summary TEXT DEFAULT '',basis TEXT DEFAULT '',status TEXT DEFAULT '待处理',note TEXT DEFAULT '',added TEXT DEFAULT CURRENT_TIMESTAMP)")
        db.execute('INSERT INTO papers(path,title) VALUES (?,?)', (str(self.source), '旧文献'))
        db.commit()
        db.close()
        migrated = Library(old)
        self.assertEqual(migrated.get(1)['title'], '旧文献')
        self.assertEqual(migrated.get(1)['description'], '')
        self.assertEqual(migrated.get(1)['end_checked'], 0)


if __name__ == '__main__':
    unittest.main()
