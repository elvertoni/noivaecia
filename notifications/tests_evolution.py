"""Tests for the Evolution API client (Fase 2). No test touches the network:
``urllib.request.urlopen`` is always mocked."""
import io
import json
import socket
import urllib.error
from unittest import mock

from django.test import SimpleTestCase, override_settings

from notifications.evolution import (
    EvolutionError,
    connect_instance_qrcode,
    get_connection_state,
    logout_instance,
    send_text,
)

FAKE_SETTINGS = dict(
    EVOLUTION_API_URL='http://work_evolution-api:8080',
    EVOLUTION_API_KEY='super-secret-key-12345',
    EVOLUTION_INSTANCE='noivascia',
)


def _urlopen_response(payload):
    """Build a mock context-manager result for ``urlopen`` returning ``payload``."""
    body = json.dumps(payload).encode('utf-8')
    response = mock.MagicMock()
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


@override_settings(**FAKE_SETTINGS)
class SendTextTests(SimpleTestCase):
    def test_send_text_returns_message_id(self):
        payload = {'key': {'id': 'ABC123', 'remoteJid': '5543999999999@s.whatsapp.net'}}
        with mock.patch('urllib.request.urlopen', return_value=_urlopen_response(payload)) as mocked:
            result = send_text('5543999999999', 'olá')
        self.assertEqual(result, 'ABC123')
        mocked.assert_called_once()

    def test_send_text_rejects_response_without_message_id(self):
        payload = {'status': 'PENDING'}
        with mock.patch('urllib.request.urlopen', return_value=_urlopen_response(payload)):
            with self.assertRaises(EvolutionError) as ctx:
                send_text('5543999999999', 'olá')
        self.assertIn('ID de mensagem', str(ctx.exception))

    def test_send_text_accepts_top_level_message_id(self):
        payload = {'messageId': 'ABC123', 'status': 'PENDING'}
        with mock.patch('urllib.request.urlopen', return_value=_urlopen_response(payload)):
            result = send_text('5543999999999', 'olá')
        self.assertEqual(result, 'ABC123')

    def test_send_text_request_shape(self):
        payload = {'key': {'id': 'XYZ'}}
        with mock.patch('urllib.request.urlopen', return_value=_urlopen_response(payload)) as mocked:
            send_text('5543999999999', 'olá mundo')
        request = mocked.call_args[0][0]
        self.assertEqual(
            request.full_url,
            'http://work_evolution-api:8080/message/sendText/noivascia',
        )
        self.assertEqual(request.get_header('Apikey'), 'super-secret-key-12345')
        self.assertEqual(request.get_header('Content-type'), 'application/json')
        sent_body = json.loads(request.data.decode('utf-8'))
        self.assertEqual(sent_body, {'number': '5543999999999', 'text': 'olá mundo'})

    def test_http_error_raises_evolution_error_with_body(self):
        error = urllib.error.HTTPError(
            url='http://work_evolution-api:8080/message/sendText/noivascia',
            code=500,
            msg='Internal Server Error',
            hdrs=None,
            fp=io.BytesIO(b'{"error": "instance not found"}'),
        )
        with mock.patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(EvolutionError) as ctx:
                send_text('5543999999999', 'olá')
        self.assertIn('500', str(ctx.exception))
        self.assertIn('instance not found', str(ctx.exception))

    def test_http_4xx_raises_evolution_error(self):
        error = urllib.error.HTTPError(
            url='http://work_evolution-api:8080/message/sendText/noivascia',
            code=401,
            msg='Unauthorized',
            hdrs=None,
            fp=io.BytesIO(b'{"error": "invalid apikey"}'),
        )
        with mock.patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(EvolutionError):
                send_text('5543999999999', 'olá')

    def test_timeout_raises_evolution_error(self):
        with mock.patch('urllib.request.urlopen', side_effect=socket.timeout('timed out')):
            with self.assertRaises(EvolutionError):
                send_text('5543999999999', 'olá')

    def test_url_error_raises_evolution_error(self):
        error = urllib.error.URLError(socket.timeout('timed out'))
        with mock.patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(EvolutionError):
                send_text('5543999999999', 'olá')

    def test_api_key_never_leaks_into_error_message(self):
        error = urllib.error.HTTPError(
            url='http://work_evolution-api:8080/message/sendText/noivascia',
            code=500,
            msg='Internal Server Error',
            hdrs=None,
            fp=io.BytesIO(b'{"error": "boom"}'),
        )
        with mock.patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(EvolutionError) as ctx:
                send_text('5543999999999', 'olá')
        self.assertNotIn(FAKE_SETTINGS['EVOLUTION_API_KEY'], str(ctx.exception))


class NotConfiguredTests(SimpleTestCase):
    """No env vars set: client must refuse before touching the network."""

    @override_settings(EVOLUTION_API_URL='', EVOLUTION_API_KEY='', EVOLUTION_INSTANCE='')
    def test_send_text_raises_when_not_configured(self):
        with mock.patch('urllib.request.urlopen') as mocked:
            with self.assertRaises(EvolutionError):
                send_text('5543999999999', 'olá')
        mocked.assert_not_called()

    @override_settings(EVOLUTION_API_URL='', EVOLUTION_API_KEY='', EVOLUTION_INSTANCE='')
    def test_get_connection_state_raises_when_not_configured(self):
        with mock.patch('urllib.request.urlopen') as mocked:
            with self.assertRaises(EvolutionError):
                get_connection_state()
        mocked.assert_not_called()

    @override_settings(
        EVOLUTION_API_URL='http://evolution:8080',
        EVOLUTION_API_KEY='',
        EVOLUTION_INSTANCE='noivascia',
    )
    def test_send_text_requires_api_key(self):
        with mock.patch('urllib.request.urlopen') as mocked:
            with self.assertRaises(EvolutionError):
                send_text('5543999999999', 'olá')
        mocked.assert_not_called()


@override_settings(**FAKE_SETTINGS)
class ConnectionStateTests(SimpleTestCase):
    def test_get_connection_state_returns_state_string(self):
        payload = {'instance': {'instanceName': 'noivascia', 'state': 'open'}}
        with mock.patch('urllib.request.urlopen', return_value=_urlopen_response(payload)):
            state = get_connection_state()
        self.assertEqual(state, 'open')

    def test_get_connection_state_http_error_raises_evolution_error(self):
        error = urllib.error.HTTPError(
            url='http://work_evolution-api:8080/instance/connectionState/noivascia',
            code=404,
            msg='Not Found',
            hdrs=None,
            fp=io.BytesIO(b'{"error": "instance not found"}'),
        )
        with mock.patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(EvolutionError):
                get_connection_state()


@override_settings(**FAKE_SETTINGS)
class ConnectInstanceQrCodeTests(SimpleTestCase):
    def test_connect_instance_qrcode_returns_base64_payload(self):
        payload = {
            'pairingCode': None,
            'code': '2@example',
            'base64': 'data:image/png;base64,abc',
            'count': 1,
        }
        with mock.patch('urllib.request.urlopen', return_value=_urlopen_response(payload)) as mocked:
            result = connect_instance_qrcode()
        self.assertEqual(result['base64'], 'data:image/png;base64,abc')
        self.assertEqual(result['code'], '2@example')
        request = mocked.call_args[0][0]
        self.assertEqual(
            request.full_url,
            'http://work_evolution-api:8080/instance/connect/noivascia',
        )

    def test_connect_instance_qrcode_prefixes_raw_base64(self):
        payload = {'base64': 'abc'}
        with mock.patch('urllib.request.urlopen', return_value=_urlopen_response(payload)):
            result = connect_instance_qrcode()
        self.assertEqual(result['base64'], 'data:image/png;base64,abc')


@override_settings(**FAKE_SETTINGS)
class LogoutInstanceTests(SimpleTestCase):
    def test_logout_instance_uses_delete_endpoint(self):
        payload = {'success': True, 'message': 'Instance logged out successfully'}
        with mock.patch('urllib.request.urlopen', return_value=_urlopen_response(payload)) as mocked:
            result = logout_instance()
        self.assertEqual(result, payload)
        request = mocked.call_args[0][0]
        self.assertEqual(request.get_method(), 'DELETE')
        self.assertEqual(
            request.full_url,
            'http://work_evolution-api:8080/instance/logout/noivascia',
        )
