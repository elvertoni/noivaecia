"""Thin HTTP client for the Evolution API (WhatsApp gateway).

Uses only the stdlib ``urllib`` — a single JSON POST/GET with a timeout does
not justify adding ``requests`` as a dependency (see CLAUDE.md). The Django
app and the Evolution API instance live on the same internal Docker Swarm
network, so the API key never travels over the public internet.
"""
import json
import socket
import urllib.error
import urllib.request

from django.conf import settings

REQUEST_TIMEOUT = 15


class EvolutionError(Exception):
    """Raised when the Evolution API is unreachable, misconfigured, or
    replies with a non-2xx status. Never includes the API key."""


def _require_config():
    api_url = getattr(settings, 'EVOLUTION_API_URL', '')
    instance = getattr(settings, 'EVOLUTION_INSTANCE', '')
    if not api_url or not instance:
        raise EvolutionError('Evolution API não configurada.')
    return api_url, instance


def _request(method, path, body=None):
    """Perform an HTTP request against the Evolution API and return the
    parsed JSON response body. Raises ``EvolutionError`` on any failure."""
    api_url, instance = _require_config()
    url = f'{api_url}{path.format(instance=instance)}'

    data = None
    headers = {'apikey': getattr(settings, 'EVOLUTION_API_KEY', '')}
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode('utf-8', errors='replace')
        raise EvolutionError(
            f'Evolution API retornou erro HTTP {exc.code}: {error_body}'
        ) from exc
    except (urllib.error.URLError, socket.timeout) as exc:
        raise EvolutionError(f'Falha ao conectar à Evolution API: {exc.reason if hasattr(exc, "reason") else exc}') from exc
    except socket.error as exc:
        raise EvolutionError(f'Falha ao conectar à Evolution API: {exc}') from exc

    if not raw:
        return {}
    try:
        return json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise EvolutionError(f'Resposta inválida da Evolution API: {exc}') from exc


def send_text(number, text):
    """Send a plain-text WhatsApp message via Evolution API.

    Returns the message id from the response (typically ``key.id``); if the
    response has no ``key.id``, returns the full response dict instead.
    """
    payload = _request(
        'POST',
        '/message/sendText/{instance}',
        body={'number': number, 'text': text},
    )
    if isinstance(payload, dict):
        message_id = payload.get('key', {}).get('id') if isinstance(payload.get('key'), dict) else None
        if message_id:
            return message_id
    return payload


def connect_instance_qrcode():
    """Start WhatsApp pairing and return QR payload data from Evolution API."""
    payload = _request('GET', '/instance/connect/{instance}')
    if not isinstance(payload, dict):
        raise EvolutionError('Evolution API não retornou dados do QR Code.')

    base64_image = payload.get('base64') or payload.get('qrcode')
    if isinstance(base64_image, dict):
        base64_image = base64_image.get('base64')
    if base64_image and not str(base64_image).startswith('data:image/'):
        base64_image = f'data:image/png;base64,{base64_image}'

    return {
        'base64': base64_image or '',
        'code': payload.get('code') or '',
        'pairing_code': payload.get('pairingCode') or '',
        'count': payload.get('count'),
    }


def get_connection_state():
    """Return the instance connection state (e.g. ``'open'``/``'close'``)."""
    payload = _request('GET', '/instance/connectionState/{instance}')
    if isinstance(payload, dict):
        instance_info = payload.get('instance')
        if isinstance(instance_info, dict) and 'state' in instance_info:
            return instance_info['state']
        if 'state' in payload:
            return payload['state']
    return payload
