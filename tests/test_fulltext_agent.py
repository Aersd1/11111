import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from library_core import Library
from fulltext_agent import analyze, extract_full, QUESTIONS, questions_from
from model_response import ModelResponseError
from markdown_ui import MarkdownEdit, markdown_html, open_typeset
from PySide6.QtWidgets import QApplication

QAPP = QApplication.instance() or QApplication([])


class FulltextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'paper.txt'
        self.source.write_text('start ' + 'experimental evidence ' * 1500 + ' END OF PAPER', 'utf-8')
        config = self.root / 'api.json'
        config.write_text('{}', 'utf-8')
        self.settings = {'api_config': str(config), 'analysis_max_calls': 32}
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def caller(self, config, instruction, payload, maximum):
        self.calls.append(payload)
        if 'segment' in payload or ('evidence' in payload and 'coverage' not in payload):
            return {'notes': 'experimental evidence [段 1]'}, 10
        return {'summary': '## 问题\n**答案** $x_i^2$', 'read_again': [1] if 'reread' not in payload else []}, 20

    def run_analysis(self, reuse=True):
        return analyze(self.source, self.settings, self.root / 'cache', reuse, caller=self.caller)

    def test_complete_text_reread_and_reuse(self):
        summary, info = self.run_analysis()
        read = [c['text'] for c in self.calls if 'segment' in c]
        self.assertEqual(''.join(read), self.source.read_text('utf-8'))
        self.assertIn('END OF PAPER', read[-1])
        self.assertEqual(info['reread'], [1])
        self.assertTrue(any('reread' in c for c in self.calls))
        self.assertIn('$x_i^2$', summary)
        self.calls.clear()
        _, second = self.run_analysis()
        self.assertEqual(second['cached_notes'], info['segments'])
        self.assertEqual(second['calls'], 2)
        self.assertFalse(any('segment' in c for c in self.calls))
        self.calls.clear()
        self.run_analysis(False)
        self.assertTrue(any('segment' in c for c in self.calls))

    def test_questions_and_source_invalidate_cache(self):
        self.run_analysis()
        self.settings['analysis_questions'] = '实验局限是什么？'
        _, info = self.run_analysis()
        self.assertEqual(info['cached_notes'], 0)
        self.source.write_text('new contents', 'utf-8')
        _, info = self.run_analysis()
        self.assertEqual(info['cached_notes'], 0)
        self.assertEqual(info['questions'], ['实验局限是什么？'])
        self.assertEqual(len(QUESTIONS), 10)
        with self.assertRaises(ValueError):
            questions_from('')

    def test_failed_segment_can_resume_from_cached_notes(self):
        def failing(config, instruction, payload, maximum):
            if payload.get('segment') == 2:
                raise ModelResponseError('模型接口等待超过 180 秒')
            return self.caller(config, instruction, payload, maximum)
        with self.assertRaisesRegex(ModelResponseError, '第 2/3 段'):
            analyze(self.source, self.settings, self.root / 'cache', caller=failing)
        self.calls.clear()
        _, info = self.run_analysis()
        self.assertEqual(info['cached_notes'], 1)
        self.assertEqual([p['segment'] for p in self.calls if 'segment' in p], [2, 3])

    def test_transient_retry_counts_against_budget(self):
        attempts = []
        def transient(config, instruction, payload, maximum):
            attempts.append(payload)
            if len(attempts) == 1:
                raise ModelResponseError('暂时断连', retryable=True)
            return self.caller(config, instruction, payload, maximum)
        with patch('fulltext_agent.time.sleep'):
            _, info = analyze(self.source, self.settings, self.root / 'cache', caller=transient)
        self.assertEqual(info['calls'], len(attempts))
        self.assertEqual(len(attempts), 6)
        self.assertEqual(attempts[0], attempts[1])

    def test_retries_never_exceed_budget(self):
        self.source.write_text('evidence', 'utf-8')
        self.settings['analysis_max_calls'] = 3
        with patch('fulltext_agent.time.sleep'), patch('agent_search.model_json') as caller:
            caller.side_effect = ModelResponseError('暂时断连', retryable=True)
            with self.assertRaises(ModelResponseError):
                analyze(self.source, self.settings, self.root / 'cache', caller=caller)
        self.assertEqual(caller.call_count, 3)

    def test_budget_rejects_without_sending_any_text(self):
        self.settings['analysis_max_calls'] = 2
        with self.assertRaisesRegex(ValueError, '未向模型发送'):
            self.run_analysis()
        self.assertFalse(self.calls)

    def test_hierarchical_reduction_reads_every_segment(self):
        self.source.write_text('a' * 85000, 'utf-8')
        _, info = self.run_analysis()
        self.assertEqual(info['segments'], 8)
        self.assertEqual(len([p for p in self.calls if 'segment' in p]), 8)
        self.assertEqual(len([p for p in self.calls if 'evidence' in p and 'coverage' not in p]), 2)

    def test_pdf_reads_all_pages_and_reports_missing_text(self):
        from types import SimpleNamespace
        pdf = self.root / 'paper.pdf'
        pdf.write_bytes(b'mocked pdf')
        pages = [SimpleNamespace(extract_text=lambda t=t: t) for t in ['first', '', 'last appendix']]
        with patch('pypdf.PdfReader', return_value=SimpleNamespace(pages=pages)):
            text, _, coverage = extract_full(pdf)
        self.assertIn('last appendix', text)
        self.assertIn('无可提取文本的页：2', coverage)

    def test_summary_history_restore_and_wrong_paper(self):
        lib = Library(self.root / 'library.sqlite3')
        ident, _ = lib.add(self.source)
        lib.update(ident, summary='old')
        lib.update(ident, summary='new', analysis_info='{"segments":3}')
        history = lib.summary_history(ident)
        self.assertEqual(history[0]['summary'], 'old')
        with self.assertRaises(ValueError):
            lib.restore_summary(ident + 1, history[0]['id'])
        lib.restore_summary(ident, history[0]['id'])
        self.assertEqual(lib.get(ident)['summary'], 'old')
        self.assertEqual(lib.summary_history(ident)[0]['summary'], 'new')

    def test_markdown_edit_and_offline_formula_preview(self):
        editor = MarkdownEdit('energy')
        editor.selectAll()
        editor.wrap('**')
        self.assertEqual(editor.toPlainText(), '**energy**')
        editor.wrap('*')
        self.assertEqual(editor.toPlainText(), '***energy***')
        text = '**bold** $x_i$ $$\\frac{a}{b}$$ <script>alert(1)</script>'
        html = markdown_html(text)
        self.assertIn('$x_i$', html)
        self.assertNotIn('<script>', html)
        self.assertIn('font-weight:700', html)
        with patch('markdown_ui.data_root', return_value=self.root), patch('markdown_ui.webbrowser.open') as opened:
            path = open_typeset(text)
        document = path.read_text('utf-8')
        self.assertIn('"trust": false', document)
        self.assertIn('katex.min.js', document)
        self.assertNotIn('https://cdn', document)
        opened.assert_called_once()

    def test_ui_worker_failure_preserves_summary_success_archives(self):
        from app import App
        lib = Library(self.root / 'library.sqlite3')
        ident, _ = lib.add(self.source)
        lib.update(ident, summary='original', major='Research', minor='A/B', category_locked=1)
        window = App(lib)
        def wait():
            deadline = time.monotonic() + 5
            while window.busy and time.monotonic() < deadline:
                QAPP.processEvents()
                window.poll()
                time.sleep(.01)
            self.assertFalse(window.busy)
        with patch('app.analyze', side_effect=ModelResponseError('第 2/3 段：模型接口等待超过 180 秒')):
            window.process_fulltext([ident])
            wait()
        self.assertEqual(lib.get(ident)['summary'], 'original')
        self.assertIn('第 2/3 段', lib.get(ident)['note'])
        info = {'segments': 3, 'calls': 4, 'cached_notes': 0}
        with patch('app.analyze', return_value=('new summary', info)):
            window.process_fulltext([ident])
            wait()
        self.assertEqual(lib.get(ident)['summary'], 'new summary')
        self.assertEqual(lib.get(ident)['minor'], 'A/B')
        self.assertEqual(lib.summary_history(ident)[0]['summary'], 'original')
        window.close()
        window.deleteLater()
        QAPP.processEvents()

    def test_settings_custom_questions_persist(self):
        from ui_settings import SettingsDialog
        lib = Library(self.root / 'library.sqlite3')
        lib.save_settings({**lib.settings(), **self.settings})
        dialog = SettingsDialog(lib)
        dialog.analysis_questions.setPlainText('什么问题？\n什么局限？')
        dialog.analysis_max_calls.setValue(24)
        dialog.save_and_use()
        self.assertEqual(lib.settings()['analysis_questions'], '什么问题？\n什么局限？')
        self.assertEqual(lib.settings()['analysis_max_calls'], 24)

    def test_settings_thinking_defaults_on_and_persists_off(self):
        from ui_settings import SettingsDialog
        from api_settings import read_config
        config = Path(self.settings['api_config'])
        config.write_text(json.dumps({'OpenAI': {'base_url': 'https://api.scnet.cn/api/llm/v1', 'api_key': 'test', 'model': 'DeepSeek-V4-Flash'}}), 'utf-8')
        lib = Library(self.root / 'library.sqlite3')
        lib.save_settings({**lib.settings(), **self.settings})
        dialog = SettingsDialog(lib)
        self.assertTrue(dialog.enable_thinking.isChecked())
        dialog.enable_thinking.setChecked(False)
        self.assertTrue(dialog.api_dirty)
        dialog.save_and_use()
        self.assertIs(read_config(config)['OpenAI']['enable_thinking'], False)
        reopened = SettingsDialog(lib)
        self.assertFalse(reopened.enable_thinking.isChecked())
        reopened.close()


if __name__ == '__main__':
    unittest.main()
