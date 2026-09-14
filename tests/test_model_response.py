import io
import json
import unittest
import urllib.error
from unittest.mock import patch
from model_response import ModelResponseError, parse_response, request_json, read_stream


def response(content, finish='stop', **message):
    return {'choices': [{'finish_reason': finish, 'message': {'content': content, **message}}]}


class ModelResponseTests(unittest.TestCase):
    def test_stream_assembles_json_and_ignores_reasoning(self):
        events = [
            {'choices': [{'index': 0, 'delta': {'reasoning_content': 'private reasoning'}}]},
            {'choices': [{'index': 0, 'delta': {'content': '{"notes":'}}]},
            {'choices': [{'index': 0, 'delta': {'content': '"证据"}'}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'total_tokens': 123}},
        ]
        raw = b': keepalive\n\n' + b''.join(('data: ' + json.dumps(e) + '\n\n').encode() for e in events) + b'data: [DONE]\n'
        stream = io.BytesIO(raw)
        stream.headers = {'Content-Type': 'text/event-stream; charset=utf-8'}
        with patch('urllib.request.urlopen', return_value=stream):
            self.assertEqual(request_json(None), ({'notes': '证据'}, 123))

    def test_stream_disconnect_never_accepts_partial_result(self):
        raw = b'data: {"choices":[{"delta":{"content":"{}"}}]}\n\ndata: [DONE]\n'
        with self.assertRaisesRegex(ModelResponseError, '断开') as caught:
            read_stream(io.BytesIO(raw))
        self.assertTrue(caught.exception.retryable)

    def test_fenced_json_and_latex(self):
        result, usage = parse_response(response('```JSON\n{"notes":"evidence"}\n```'))
        self.assertEqual(result['notes'], 'evidence')
        self.assertEqual(usage, 0)
        result, _ = parse_response(response(r'{"notes":"$\alpha + \sigma$"}'))
        self.assertEqual(result['notes'], r'$\alpha + \sigma$')
        result, _ = parse_response(response(json.dumps({'notes': r'$\frac{a}{b}$'})))
        self.assertEqual(result['notes'], r'$\frac{a}{b}$')

    def test_incomplete_and_non_object_responses_are_rejected(self):
        for content in ['{"notes":"unfinished', '[]', '{"notes":"bad" trailing}', r'{"notes":"\alpha']:
            with self.subTest(content=content), self.assertRaises(ModelResponseError):
                parse_response(response(content))
        with self.assertRaisesRegex(ModelResponseError, '截断'):
            parse_response(response('{"notes":"partial"}', 'length'))
        with self.assertRaisesRegex(ModelResponseError, '推理'):
            parse_response(response(None, reasoning_content='reasoning'))

    def test_timeout_http_and_invalid_envelope_are_safe(self):
        for error, message in [(TimeoutError('SECRET'), '180 秒'),
                               (urllib.error.URLError(TimeoutError('SECRET')), '180 秒'),
                               (urllib.error.HTTPError('secret-url', 429, 'SECRET', {}, None), '429'),
                               (OSError('SECRET'), '无法连接')]:
            with self.subTest(error=error), patch('urllib.request.urlopen', side_effect=error):
                with self.assertRaisesRegex(ModelResponseError, message) as caught:
                    request_json(None)
                self.assertNotIn('SECRET', str(caught.exception))
        for document in [{}, {'choices': []}, {'choices': [None]}]:
            with self.subTest(document=document), self.assertRaises(ModelResponseError):
                parse_response(document)

    def test_long_request_timeout_and_usage(self):
        document = response('{"notes":"done"}')
        document['usage'] = {'total_tokens': 42}
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(document).encode())) as send:
            self.assertEqual(request_json(None), ({'notes': 'done'}, 42))
        self.assertEqual(send.call_args.kwargs['timeout'], 180)

    def test_regular_request_retries_only_transient_errors(self):
        raw = json.dumps(response('{"summary":"complete"}')).encode()
        with patch('model_response.time.sleep'), patch('urllib.request.urlopen', side_effect=[TimeoutError(), io.BytesIO(raw)]) as send:
            self.assertEqual(request_json(None, attempts=3)[0]['summary'], 'complete')
        self.assertEqual(send.call_count, 2)
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{}')) as send:
            with self.assertRaises(ModelResponseError):
                request_json(None, attempts=3)
        self.assertEqual(send.call_count, 1)
