import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api_settings import read_config, write_config, validate_config
from library_core import call_model
from agent_search import model_json

CONFIG = {'OpenAI': {'base_url': 'https://example.invalid/v1', 'api_key': 'test-only-key', 'model': 'old-model',
    'temperature': 0.7, 'max_tokens': 10000, 'embedding_model': 'keep-me', 'stream': False}, 'extra': {'keep': True}}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'api.json'
        self.path.write_text(json.dumps(CONFIG), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_save_preserves_extra_and_creates_previous_version_backup(self):
        changed = read_config(self.path)
        changed['OpenAI']['model'] = 'new-model'
        write_config(self.path, changed)
        self.assertEqual(read_config(self.path)['OpenAI']['model'], 'new-model')
        self.assertEqual(read_config(self.path)['extra'], CONFIG['extra'])
        self.assertEqual(read_config(self.path.with_suffix('.json.bak')), CONFIG)

    def test_invalid_save_leaves_original_bytes_untouched(self):
        previous = self.path.read_bytes()
        invalid = copy.deepcopy(CONFIG)
        invalid['OpenAI']['base_url'] = 'http://remote.invalid/v1'
        with self.assertRaises(ValueError):
            write_config(self.path, invalid)
        self.assertEqual(self.path.read_bytes(), previous)

    def test_atomic_failure_leaves_original_usable(self):
        previous = self.path.read_bytes()
        with patch('api_settings.os.replace', side_effect=OSError('write failure')):
            with self.assertRaises(ValueError):
                write_config(self.path, CONFIG)
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_url_and_numeric_validation(self):
        for url in ['http://localhost.evil.invalid/v1', 'https://u:p@somewhere.invalid', 'https://x.invalid/v1?key=secret']:
            bad = copy.deepcopy(CONFIG)
            bad['OpenAI']['base_url'] = url
            with self.assertRaises(ValueError):
                validate_config(bad)
        for value in [float('nan'), 3, -1, True]:
            bad = copy.deepcopy(CONFIG)
            bad['OpenAI']['temperature'] = value
            with self.assertRaises(ValueError):
                validate_config(bad)

    def test_new_saved_model_and_key_are_used_by_next_request(self):
        changed = read_config(self.path)
        changed['OpenAI'].update(model='new-model', api_key='new-test-key', base_url='https://new.invalid/v1')
        write_config(self.path, changed)
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({'choices': [{'message': {'content': json.dumps({'major': 'A', 'minor': 'B', 'summary': 'Summary', 'uncertain': False})}}]}).encode()
        with patch('library_core.urllib.request.urlopen', return_value=Response()) as send:
            call_model(self.path, {'title': 'Test'})
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, 'https://new.invalid/v1/chat/completions')
        self.assertEqual(json.loads(request.data)['model'], 'new-model')
        self.assertEqual(request.get_header('Authorization'), 'Bearer new-test-key')

    def test_thinking_defaults_on_and_saved_switch_reaches_both_callers(self):
        import io
        cfg = copy.deepcopy(CONFIG)
        cfg['OpenAI'].update(base_url='https://api.scnet.cn/api/llm/v1', model='DeepSeek-V4-Flash-0731')
        raw = json.dumps({'choices': [{'message': {'content': json.dumps({'major': 'A', 'minor': 'B', 'summary': 'Summary', 'uncertain': False})}}]}).encode()
        for thinking in (None, False, True):
            if thinking is not None:
                cfg['OpenAI']['enable_thinking'] = thinking
            write_config(self.path, cfg)
            for call in (lambda: call_model(self.path, {}), lambda: model_json(self.path, 'JSON', {}, 1000)):
                with patch('urllib.request.urlopen', return_value=io.BytesIO(raw)) as send:
                    call()
                self.assertEqual(json.loads(send.call_args.args[0].data)['enable_thinking'], thinking is not False)

    def test_invalid_thinking_setting_is_rejected(self):
        for value in ('false', 0, None):
            cfg = copy.deepcopy(CONFIG)
            cfg['OpenAI']['enable_thinking'] = value
            with self.assertRaises(ValueError):
                validate_config(cfg)


if __name__ == '__main__':
    unittest.main()
