"""Generate a deterministic, private, read-only reconciliation report."""

import json
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.legacy_reconciliation import (
    build_reconciliation_report,
    reconciliation_aliases,
)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (
        hasattr(path, 'is_junction') and path.is_junction()
    )


def _assert_no_links(root: Path, path: Path) -> None:
    current = root
    if _is_link(current):
        raise ValueError(f'Caminho não pode ser link simbólico: {current}')
    for part in path.relative_to(root).parts:
        current = current / part
        if _is_link(current):
            raise ValueError(f'Caminho não pode ser link simbólico: {current}')


def _assert_absolute_chain_no_links(path: Path) -> None:
    """Reject links/junctions in every existing component of an absolute path."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if _is_link(current):
            raise ValueError(f'Caminho não pode ser link simbólico: {current}')


def resolve_manifest_output(output: str) -> tuple[Path, Path]:
    backup_root = Path(os.path.abspath(Path(settings.BACKUP_ROOT).expanduser()))
    root = backup_root.parent / 'recovery-manifests'
    supplied = Path(output).expanduser()
    candidate = supplied if supplied.is_absolute() else root / supplied
    candidate = Path(os.path.abspath(candidate))
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f'--output deve ficar dentro de {root}') from exc
    if candidate == root:
        raise ValueError('--output deve apontar para um arquivo.')

    _assert_absolute_chain_no_links(root.parent)
    if not root.parent.exists():
        root.parent.mkdir(parents=True, exist_ok=True)
    _assert_absolute_chain_no_links(root.parent)
    root.mkdir(exist_ok=True)
    _assert_no_links(root, candidate)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_links(root, candidate)
    return root, candidate


def atomic_write_manifest(output: Path, payload: str, *, root: Path) -> None:
    """Write mode 0600 beside destination, then atomically replace it."""
    _assert_no_links(root, output)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f'.{output.name}.',
        suffix='.tmp',
        dir=output.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output_file:
            descriptor = -1
            output_file.write(payload)
            output_file.flush()
            os.fsync(output_file.fileno())
        _assert_no_links(root, output)
        os.replace(temporary, output)
        os.chmod(output, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.lexists(temporary):
            os.unlink(temporary)


class Command(BaseCommand):
    help = (
        'Compara clones isolados do banco atual e do backup curado. '
        'Esta fase é sempre dry-run e não altera bancos.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--target-db-name', required=True)
        parser.add_argument('--curated-db-name', required=True)
        parser.add_argument('--output', required=True)
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Não suportado; existe apenas para recusar aplicação acidental.',
        )

    def handle(self, *args, **options):
        if options['apply']:
            raise CommandError(
                '--apply não é suportado. O reconcile_legacy é estritamente dry-run.'
            )
        try:
            manifest_root, output_path = resolve_manifest_output(options['output'])
            with reconciliation_aliases(
                target_name=options['target_db_name'],
                curated_name=options['curated_db_name'],
            ) as (target_alias, curated_alias):
                report = build_reconciliation_report(target_alias, curated_alias)
            payload = (
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
                + '\n'
            )
            atomic_write_manifest(output_path, payload, root=manifest_root)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            f'Dry-run concluído: {output_path} · SHA-256 {report["report_sha256"]}'
        ))
