"""
Fetches all Brazilian municipalities from IBGE API and writes
static/js/municipios-br.js for use in the customer city datalist.

Usage:
    python manage.py generate_municipios_js
    python manage.py generate_municipios_js --output static/js/municipios-br.js
"""

import gzip
import json
import urllib.request
import urllib.error
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

_IBGE_UFS = 'https://servicodados.ibge.gov.br/api/v1/localidades/estados?orderBy=sigla'
_IBGE_MUNICIPIOS = (
    'https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios?orderBy=nome'
)
_DEFAULT_OUTPUT = 'static/js/municipios-br.js'
_TIMEOUT = 30


def _fetch_json(url):
    req = urllib.request.Request(url, headers={'Accept-Encoding': 'gzip, deflate'})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
            encoding = resp.headers.get('Content-Encoding', '')
            if encoding == 'gzip' or raw[:2] == b'\x1f\x8b':
                raw = gzip.decompress(raw)
            return json.loads(raw.decode('utf-8'))
    except urllib.error.URLError as exc:
        raise CommandError(f'Erro de rede ao acessar {url}: {exc}') from exc


class Command(BaseCommand):
    help = 'Gera static/js/municipios-br.js com todos os 5.570 municípios do Brasil via API IBGE.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default=_DEFAULT_OUTPUT,
            help=f'Caminho do arquivo JS gerado (padrão: {_DEFAULT_OUTPUT})',
        )

    def handle(self, *args, **options):
        output_path = Path(options['output'])
        output_path.parent.mkdir(parents=True, exist_ok=True)

        self.stdout.write('Buscando lista de UFs no IBGE...')
        ufs = _fetch_json(_IBGE_UFS)
        self.stdout.write(f'  {len(ufs)} estados encontrados.')

        cidades = {}
        for uf_data in ufs:
            sigla = uf_data['sigla']
            url = _IBGE_MUNICIPIOS.format(uf=sigla)
            municipios = _fetch_json(url)
            cidades[sigla] = [m['nome'] for m in municipios]
            self.stdout.write(f'  {sigla}: {len(cidades[sigla])} municípios')

        total = sum(len(v) for v in cidades.values())
        data_json = json.dumps(cidades, ensure_ascii=False, separators=(',', ':'))

        js = (
            '/* Municípios do Brasil — gerado por: python manage.py generate_municipios_js */\n'
            '/* Fonte: IBGE Localidades API v1 */\n'
            f'const CIDADES_BR={data_json};\n'
        )

        output_path.write_text(js, encoding='utf-8')
        size_kb = output_path.stat().st_size // 1024

        self.stdout.write(
            self.style.SUCCESS(
                f'\nGerado: {output_path} — {total} municípios, {len(cidades)} estados, {size_kb} KB'
            )
        )
