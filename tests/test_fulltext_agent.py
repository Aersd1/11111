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
        return {'answers': [{'id': q['id'], 'answer': '证据支持的答案 $x_i^2$'} for q in payload['questions']],
                'read_again': [1]}, 20

    def run_analysis(self, reuse=True, extra=''):
        return analyze(self.source, self.settings, self.root / 'cache', reuse,
                       caller=self.caller, supplementary_questions=extra)

    def test_full_text_is_sent_once_without_reread_and_result_is_reused(self):
        summary, info = self.run_analysis()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['full_text'], self.source.read_text('utf-8'))
        self.assertIn('END OF PAPER', self.calls[0]['full_text'])
        self.assertEqual(info['calls'], 1)
        self.assertIn('$x_i^2$', summary)
        self.calls.clear()
        same, cached = self.run_analysis()
        self.assertEqual(same, summary)
        self.assertTrue(cached['cached_result'])
        self.assertEqual(cached['calls'], 0)
        self.assertEqual(self.calls, [])
        self.run_analysis(False)
        self.assertEqual(len(self.calls), 1)

    def test_questions_source_and_model_config_invalidate_cache(self):
        self.run_analysis()
        self.settings['analysis_questions'] = '实验局限是什么？'
        _, info = self.run_analysis()
        self.assertFalse(info['cached_result'])
        self.source.write_text('new contents', 'utf-8')
        _, info = self.run_analysis()
        self.assertFalse(info['cached_result'])
        Path(self.settings['api_config']).write_text('{"model":"changed"}', 'utf-8')
        _, info = self.run_analysis()
        self.assertFalse(info['cached_result'])
        self.assertEqual(info['questions'], ['实验局限是什么？'])
        with self.assertRaises(ValueError):
            questions_from('')

    def test_supplementary_questions_are_deduplicated_and_invalidate_cache(self):
        _, info = self.run_analysis(extra=QUESTIONS[0] + '\n适用于我的实验吗？')
        self.assertEqual(len(info['questions']), len(QUESTIONS) + 1)
        self.assertEqual(info['questions'][-1], '适用于我的实验吗？')
        self.assertEqual(len(self.calls), 1)
        _, changed = self.run_analysis(extra='还有其他证据吗？')
        self.assertFalse(changed['cached_result'])
        with self.assertRaises(ValueError):
            self.run_analysis(extra='x' * 6001)

    def test_transient_errors_do_not_repeat_requests(self):
        from unittest.mock import Mock
        caller = Mock(side_effect=ModelResponseError('暂时断连', retryable=True))
        with self.assertRaisesRegex(ModelResponseError, '不自动重复请求'):
            analyze(self.source, self.settings, self.root / 'cache', caller=caller)
        self.assertEqual(caller.call_count, 1)
        self.assertFalse(list((self.root / 'cache').rglob('answers.json')))

    def test_missing_answers_are_not_saved_or_retried(self):
        from unittest.mock import Mock
        caller = Mock(return_value=({'answers': [{'id': 1, 'answer': 'partial'}]}, 10))
        with self.assertRaisesRegex(ValueError, '未回答全部问题'):
            analyze(self.source, self.settings, self.root / 'cache', caller=caller)
        self.assertEqual(caller.call_count, 1)
        self.assertFalse(list((self.root / 'cache').rglob('answers.json')))

    def test_long_document_never_splits_or_truncates(self):
        self.source.write_text('a' * 250000 + ' LAST PAGE', 'utf-8')
        self.settings['analysis_max_calls'] = 1
        _, info = self.run_analysis()
        self.assertEqual(info['calls'], 1)
        self.assertEqual(self.calls[0]['full_text'], self.source.read_text('utf-8'))

    def test_invalid_cache_is_replaced_by_one_complete_request(self):
        self.run_analysis()
        cache = next((self.root / 'cache').rglob('answers.json'))
        cache.write_text('{"answers":[]}', 'utf-8')
        self.calls.clear()
        _, info = self.run_analysis()
        self.assertEqual(info['calls'], 1)
        self.assertEqual(len(self.calls), 1)

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
        lib.update(ident, summary='original', major='Research', minor='A/B', category_locked=1,
                   analysis_extra_questions='适用于我的实验吗？', description='PRIVATE NOTE')
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
        info = {'questions': QUESTIONS, 'calls': 1, 'cached_result': False}
        with patch('app.analyze', return_value=('new summary', info)) as call:
            window.process_fulltext([ident])
            wait()
        self.assertEqual(call.call_args.kwargs['supplementary_questions'].strip(), '适用于我的实验吗？')
        self.assertNotIn('PRIVATE NOTE', repr(call.call_args))
        self.assertEqual(lib.get(ident)['analysis_extra_questions'], '适用于我的实验吗？')
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
        dialog.save_and_use()
        self.assertEqual(lib.settings()['analysis_questions'], '什么问题？\n什么局限？')

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
