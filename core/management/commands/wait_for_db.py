import time

from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = (
        'Bloqueia até o banco de dados aceitar conexões, ou falha após um '
        'tempo máximo. Roda antes de `migrate` no entrypoint para evitar '
        'crash-loop quando o container do Postgres ainda está subindo.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--timeout', type=int, default=30,
            help='Segundos máximos de espera antes de desistir (padrão: 30).',
        )
        parser.add_argument(
            '--interval', type=float, default=1.0,
            help='Segundos entre tentativas (padrão: 1).',
        )

    def handle(self, *args, **options):
        timeout = options['timeout']
        interval = options['interval']
        deadline = time.monotonic() + timeout
        connection = connections['default']

        attempt = 0
        while True:
            attempt += 1
            try:
                connection.ensure_connection()
                self.stdout.write(self.style.SUCCESS(
                    f'Banco de dados disponível (tentativa {attempt}).'
                ))
                return
            except OperationalError as exc:
                connection.close()
                if time.monotonic() >= deadline:
                    self.stderr.write(self.style.ERROR(
                        f'Banco de dados indisponível após {timeout}s: {exc}'
                    ))
                    raise
                self.stdout.write(
                    f'Banco de dados indisponível (tentativa {attempt}), '
                    f'tentando de novo em {interval}s...'
                )
                time.sleep(interval)
