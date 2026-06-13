import hashlib
import json
import shutil
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


class Command(BaseCommand):
    help = 'Create a go-live backup before Sprint R14.09 production go-live.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            default='var/backups',
            help='Directory to write backup files (default: var/backups).',
        )
        parser.add_argument(
            '--export-dir',
            default='var/legacy_export',
            help='Directory containing the legacy export manifest (default: var/legacy_export).',
        )

    def handle(self, *args, **options):
        db_config = settings.DATABASES['default']
        if db_config.get('ENGINE') != 'django.db.backends.sqlite3':
            raise CommandError(
                'golive_backup only supports SQLite. '
                f"Current engine: {db_config.get('ENGINE')}"
            )

        db_path = Path(db_config['NAME']).resolve()
        if not db_path.exists():
            raise CommandError(f'Database file not found: {db_path}')

        now = timezone.now()
        stamp = now.strftime('%Y-%m-%d-%H-%M-%S')

        output_dir = Path(options['output_dir'])
        output_dir.mkdir(parents=True, exist_ok=True)

        backup_path = output_dir / f'golive-{stamp}.sqlite3'
        manifest_path = output_dir / f'golive-{stamp}-manifest.json'

        self.stdout.write(f'Copying database to {backup_path} …')
        shutil.copy2(db_path, backup_path)

        sqlite_sha256 = _sha256(backup_path)
        sqlite_size = backup_path.stat().st_size

        mdb_sha256 = None
        mdb_exported_at = None
        legacy_manifest_path = Path(options['export_dir']) / 'manifest.json'
        if legacy_manifest_path.exists():
            try:
                with open(legacy_manifest_path, encoding='utf-8') as fh:
                    legacy = json.load(fh)
                mdb_sha256 = legacy.get('source_mdb_sha256') or legacy.get('mdb_sha256')
                mdb_exported_at = legacy.get('exported_at')
            except Exception as exc:
                self.stderr.write(f'Warning: could not read legacy manifest: {exc}')

        django_counts = {
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

        manifest = {
            'backup_at': now.isoformat(),
            'sqlite_source': str(db_path),
            'sqlite_backup': str(backup_path.resolve()),
            'sqlite_sha256': sqlite_sha256,
            'sqlite_size': sqlite_size,
            'mdb_sha256': mdb_sha256,
            'mdb_exported_at': mdb_exported_at,
            'django_counts': django_counts,
            'notes': '',
        }

        manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False)

        with open(manifest_path, 'w', encoding='utf-8') as fh:
            fh.write(manifest_json)
            fh.write('\n')

        self.stdout.write(manifest_json)
        self.stdout.write(self.style.SUCCESS(f'\nManifest written to {manifest_path}'))
