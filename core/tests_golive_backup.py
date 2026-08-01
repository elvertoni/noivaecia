from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from core.management.commands.golive_backup import _validate_backup_location


class GoLiveBackupTests(SimpleTestCase):
    def test_rejects_output_directory_inside_media_root(self):
        with TemporaryDirectory() as temp_dir:
            media_root = Path(temp_dir) / 'media'

            with self.assertRaisesMessage(
                CommandError,
                '--output-dir não pode ficar dentro de MEDIA_ROOT',
            ):
                _validate_backup_location(
                    media_root / 'backups',
                    media_root,
                )

    def test_accepts_output_directory_outside_media_root(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            _validate_backup_location(
                root / 'backups',
                root / 'media',
            )
