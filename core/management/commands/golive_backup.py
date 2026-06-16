import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from billing.models import CashAccount, FinancialMovement, Payment, Receivable
from catalog.models import Category, Product
from customers.models import Customer
from movements.models import Pickup, Return
from rentals.models import Rental, RentalItem


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _backup_sqlite(source_path: Path, backup_path: Path) -> None:
    import sqlite3

    source = sqlite3.connect(str(source_path))
    try:
        source.execute('PRAGMA query_only = ON')
        target = sqlite3.connect(str(backup_path))
        try:
            with target:
                source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _backup_postgres(db_config: dict, backup_path: Path) -> None:
    env = os.environ.copy()
    password = db_config.get('PASSWORD') or ''
    if password:
        env['PGPASSWORD'] = password

    cmd = [
        'pg_dump',
        '--host', db_config.get('HOST') or 'localhost',
        '--port', str(db_config.get('PORT') or 5432),
        '--username', db_config.get('USER') or '',
        '--dbname', db_config.get('NAME') or '',
        '--format', 'custom',
        '--no-password',
        '--file', str(backup_path),
    ]

    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise CommandError(f'pg_dump falhou (exit {result.returncode}):\n{result.stderr}')


def _archive_media(media_root: Path, archive_path: Path) -> None:
    with tarfile.open(archive_path, 'w:gz') as tar:
        tar.add(media_root, arcname='media')


class Command(BaseCommand):
    help = 'Cria backup do banco (SQLite ou PostgreSQL), da mídia e um manifesto JSON.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            default=None,
            help='Diretório para os arquivos de backup (padrão: BACKUP_ROOT).',
        )
        parser.add_argument(
            '--skip-media',
            action='store_true',
            help='Pula o backup do diretório de mídia.',
        )

    def handle(self, *args, **options):
        db_config = settings.DATABASES['default']
        engine = db_config.get('ENGINE', '')

        now = timezone.now()
        stamp = now.strftime('%Y-%m-%d-%H-%M-%S')

        output_dir = Path(options['output_dir'] or settings.BACKUP_ROOT)
        output_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            'backup_at': now.isoformat(),
            'database': None,
            'database_backup': None,
            'database_sha256': None,
            'media_backup': None,
            'media_sha256': None,
            'counts': None,
        }

        # — banco —
        if 'sqlite3' in engine:
            db_path = Path(db_config['NAME']).resolve()
            if not db_path.exists():
                raise CommandError(f'Arquivo SQLite não encontrado: {db_path}')
            backup_path = output_dir / f'noivas-{stamp}.sqlite3'
            self.stdout.write(f'Backup SQLite → {backup_path}')
            _backup_sqlite(db_path, backup_path)
            shutil.copystat(db_path, backup_path)
            manifest['database'] = 'sqlite'

        elif 'postgresql' in engine:
            backup_path = output_dir / f'noivas-{stamp}.dump'
            self.stdout.write(f'Backup PostgreSQL → {backup_path}')
            _backup_postgres(db_config, backup_path)
            manifest['database'] = 'postgresql'

        else:
            raise CommandError(f'Engine não suportada: {engine}')

        manifest['database_backup'] = str(backup_path)
        manifest['database_sha256'] = _sha256(backup_path)

        # — mídia —
        if not options['skip_media']:
            media_root = Path(getattr(settings, 'MEDIA_ROOT', settings.BASE_DIR / 'media'))
            if media_root.exists() and any(media_root.iterdir()):
                media_path = output_dir / f'media-{stamp}.tar.gz'
                self.stdout.write(f'Arquivando mídia → {media_path}')
                _archive_media(media_root, media_path)
                manifest['media_backup'] = str(media_path)
                manifest['media_sha256'] = _sha256(media_path)
            else:
                self.stdout.write('Diretório de mídia vazio ou ausente — pulando.')

        # — contagens —
        manifest['counts'] = {
            'customers': Customer.objects.count(),
            'categories': Category.objects.count(),
            'products': Product.objects.count(),
            'rentals': Rental.objects.count(),
            'rental_items': RentalItem.objects.count(),
            'pickups': Pickup.objects.count(),
            'returns': Return.objects.count(),
            'receivables': Receivable.objects.count(),
            'payments': Payment.objects.count(),
            'financial_movements': FinancialMovement.objects.count(),
            'cash_accounts': CashAccount.objects.count(),
        }

        manifest_path = output_dir / f'noivas-{stamp}-manifest.json'
        manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False)

        with open(manifest_path, 'w', encoding='utf-8') as fh:
            fh.write(manifest_json)
            fh.write('\n')

        self.stdout.write(manifest_json)
        self.stdout.write(self.style.SUCCESS(f'\nManifesto escrito em {manifest_path}'))
