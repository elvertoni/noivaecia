import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management.base import CommandError
from django.test import SimpleTestCase
from django.utils import timezone

from core.management.commands.golive_backup import (
    _prune_old_backups,
    _validate_backup_location,
)


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


class PruneOldBackupsTests(SimpleTestCase):
    def _touch(self, path, age_days):
        path.write_text('x')
        old_time = time.time() - age_days * 86400
        os.utime(path, (old_time, old_time))

    def test_removes_only_files_past_retention(self):
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            old_dump = output_dir / 'noivas-2026-01-01-00-00-00.dump'
            old_manifest = output_dir / 'noivas-2026-01-01-00-00-00-manifest.json'
            recent_dump = output_dir / 'noivas-2026-08-01-00-00-00.dump'
            self._touch(old_dump, age_days=30)
            self._touch(old_manifest, age_days=30)
            self._touch(recent_dump, age_days=1)

            removed = _prune_old_backups(output_dir, keep_days=14, now=timezone.now())

            self.assertCountEqual(removed, [str(old_dump), str(old_manifest)])
            self.assertFalse(old_dump.exists())
            self.assertFalse(old_manifest.exists())
            self.assertTrue(recent_dump.exists())

    def test_ignores_files_outside_naming_convention(self):
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            unrelated = output_dir / 'other-file.txt'
            self._touch(unrelated, age_days=30)

            removed = _prune_old_backups(output_dir, keep_days=14, now=timezone.now())

            self.assertEqual(removed, [])
            self.assertTrue(unrelated.exists())

    def test_zero_keep_days_disables_pruning(self):
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            old_dump = output_dir / 'noivas-2026-01-01-00-00-00.dump'
            self._touch(old_dump, age_days=30)

            removed = _prune_old_backups(output_dir, keep_days=0, now=timezone.now())

            self.assertEqual(removed, [])
            self.assertTrue(old_dump.exists())
