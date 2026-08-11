"""Apply the approved Access-primary financial recovery exactly once."""

import hashlib
import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from billing.models import FinancialMovement, Payment, Receivable
from billing.services import (
    reconcile_overpayment,
    register_recovered_legacy_payment,
    write_off_receivables,
)


APPROVED_MANIFEST_SHA256 = (
    '4eadf81eb841647c44883552b9cecd4dc93a412e673f6e61fbd94b0f741d2ff0'
)
APPROVED_SUMMARY = (110, Decimal('7670.00'), 25100)
APPROVED_PAYMENT_GROUPS = Counter({
    'legacy_receivable': 107,
    'full_rental_signature': 3,
})
APPROVED_WRITE_OFF_GROUPS = (25059, 40, 1)
RECOVERY_NOTE = 'Recuperação auditada da VPS de 01/08/2026 · evento #{event_id}.'
CUTOFF_REASON = 'Baixa aprovada da carteira legada até 17/07/2026.'
SETTLED_REASON = 'Baixa aprovada: título Access quitado sem valor recuperável.'
OVERPAYMENT_REASON = 'Ajuste aprovado de crédito legado excedente.'


def manifest_root() -> Path:
    return Path(settings.BACKUP_ROOT).resolve().parent / 'recovery-manifests'


def _canonical_sha256(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def load_approved_manifest(path: Path) -> dict:
    root = manifest_root()
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as error:
        raise CommandError('Manifesto deve existir dentro de recovery-manifests.') from error
    if resolved.is_symlink():
        raise CommandError('Manifesto não pode ser link simbólico.')

    try:
        manifest = json.loads(resolved.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise CommandError(f'Não foi possível ler o manifesto: {error}') from error
    if not isinstance(manifest, dict):
        raise CommandError('Manifesto inválido.')

    declared_sha256 = manifest.pop('sha256', None)
    actual_sha256 = _canonical_sha256(manifest)
    if declared_sha256 != actual_sha256:
        raise CommandError('SHA-256 interno do manifesto não confere.')
    if actual_sha256 != APPROVED_MANIFEST_SHA256:
        raise CommandError('Manifesto não corresponde ao plano aprovado.')
    return manifest


def validate_manifest(manifest: dict) -> tuple[list[dict], date, list[int], list[int]]:
    try:
        if manifest['mode'] != 'access-primary-recovery':
            raise ValueError('mode')
        cutoff_date = date.fromisoformat(manifest['cutoff_date'])
        payments = manifest['payments']
        write_offs = manifest['write_offs']
        settled_ids = write_offs['access_settled_without_value_ids']
        overpayment_ids = write_offs['legacy_overpayment_ids']
    except (KeyError, TypeError, ValueError) as error:
        raise CommandError('Estrutura do manifesto inválida.') from error

    if not isinstance(payments, list) or not all(isinstance(item, dict) for item in payments):
        raise CommandError('Pagamentos do manifesto inválidos.')
    if not all(isinstance(item, int) and item > 0 for item in settled_ids + overpayment_ids):
        raise CommandError('Identidades de baixa inválidas.')
    if len(settled_ids) != len(set(settled_ids)) or len(overpayment_ids) != len(set(overpayment_ids)):
        raise CommandError('Identidades de baixa duplicadas.')

    payment_total = Decimal('0.00')
    groups = Counter()
    source_payment_ids = set()
    receivable_ids = set()
    for payment in payments:
        try:
            source_id = payment['source_payment']['id']
            receivable_id = payment['candidate_legacy']['id']
            source = payment['candidate_legacy']['source']
            amount = Decimal(payment['amount'])
            payment_date = date.fromisoformat(payment['payment_date'])
            method = payment['method']
            discount = Decimal(payment['discount_amount'])
            interest = Decimal(payment['interest_amount'])
            group = payment['group']
        except (KeyError, TypeError, ValueError) as error:
            raise CommandError('Pagamento do manifesto inválido.') from error
        if (
            not isinstance(source_id, int)
            or not isinstance(receivable_id, int)
            or source != 'pagar'
            or amount <= 0
            or discount != 0
            or interest != 0
            or method not in Payment.Method.values
            or group not in {'legacy_receivable', 'full_rental_signature'}
            or payment_date > date.today()
        ):
            raise CommandError('Pagamento do manifesto fora da política aprovada.')
        if source_id in source_payment_ids or receivable_id in receivable_ids:
            raise CommandError('Pagamento duplicado no manifesto.')
        source_payment_ids.add(source_id)
        receivable_ids.add(receivable_id)
        payment_total += amount
        groups[group] += 1

    payment_count, expected_total, write_off_count = APPROVED_SUMMARY
    if (
        len(payments) != payment_count
        or payment_total != expected_total
        or groups != APPROVED_PAYMENT_GROUPS
        or len(settled_ids) + len(overpayment_ids) >= write_off_count
    ):
        raise CommandError('Resumo do manifesto diverge do plano aprovado.')
    return payments, cutoff_date, settled_ids, overpayment_ids


def resolve_recovery_targets(payments, cutoff_date, settled_ids, overpayment_ids):
    legacy_ids = [item['candidate_legacy']['id'] for item in payments]
    receivables = {
        receivable.legacy_id: receivable
        for receivable in Receivable.objects.filter(
            legacy_source='pagar', legacy_id__in=legacy_ids,
        )
    }
    if len(receivables) != len(legacy_ids):
        raise CommandError('Há pagamento sem recebível legado correspondente.')
    for payment in payments:
        receivable = receivables[payment['candidate_legacy']['id']]
        if receivable.paid_amount != Decimal(payment['amount']):
            raise CommandError('Valor importado diverge do pagamento aprovado.')

    cutoff_receivables = list(Receivable.objects.filter(
        balance__gt=0,
        written_off_at__isnull=True,
        due_date__lte=cutoff_date,
    ))
    settled_receivables = list(Receivable.objects.filter(
        legacy_source='pagar',
        legacy_id__in=settled_ids,
        balance__gt=0,
        written_off_at__isnull=True,
    ))
    overpayment_receivables = list(Receivable.objects.filter(
        legacy_source='pagar',
        legacy_id__in=overpayment_ids,
        balance__lt=0,
        written_off_at__isnull=True,
    ))
    if len(settled_receivables) != len(settled_ids):
        raise CommandError('Há baixa Access sem recebível aberto correspondente.')
    if len(overpayment_receivables) != len(overpayment_ids):
        raise CommandError('Há crédito legado sem recebível correspondente.')

    payment_count, _total, write_off_count = APPROVED_SUMMARY
    if len(cutoff_receivables) + len(settled_receivables) + len(overpayment_receivables) != write_off_count:
        raise CommandError('Quantidade de baixas diverge do plano aprovado.')
    if Payment.objects.exists() or Receivable.objects.filter(written_off_at__isnull=False).exists():
        raise CommandError('A base já possui recuperação financeira aplicada.')
    if FinancialMovement.objects.filter(source=FinancialMovement.Source.PAYMENT).exists():
        raise CommandError('A base já possui movimentos de pagamentos registrados.')
    if len(receivables) != payment_count:
        raise CommandError('Quantidade de pagamentos diverge do plano aprovado.')
    return receivables, cutoff_receivables, settled_receivables, overpayment_receivables


class Command(BaseCommand):
    help = 'Aplica uma única vez a recuperação financeira aprovada do Access.'

    def add_arguments(self, parser):
        parser.add_argument('--manifest', required=True)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--confirm', action='store_true')

    def handle(self, *args, **options):
        manifest = load_approved_manifest(Path(options['manifest']))
        payments, cutoff_date, settled_ids, overpayment_ids = validate_manifest(manifest)
        targets = resolve_recovery_targets(
            payments,
            cutoff_date,
            settled_ids,
            overpayment_ids,
        )
        _receivables, cutoff, settled, overpayments = targets
        self.stdout.write(
            'Plano validado: 110 pagamentos (R$ 7.670,00); '
            f'{len(cutoff) + len(settled) + len(overpayments)} baixas.'
        )
        if not options['apply']:
            self.stdout.write(self.style.WARNING('DRY-RUN: nenhuma alteração aplicada.'))
            return
        if not options['confirm']:
            raise CommandError('Use --confirm junto com --apply para executar.')

        with transaction.atomic():
            targets = resolve_recovery_targets(
                payments,
                cutoff_date,
                settled_ids,
                overpayment_ids,
            )
            receivables, cutoff, settled, overpayments = targets
            for payment in payments:
                event_id = payment['source_payment']['id']
                register_recovered_legacy_payment(
                    receivables[payment['candidate_legacy']['id']],
                    amount=payment['amount'],
                    payment_date=date.fromisoformat(payment['payment_date']),
                    method=payment['method'],
                    notes=RECOVERY_NOTE.format(event_id=event_id),
                )
            cutoff_count = write_off_receivables(cutoff, CUTOFF_REASON)
            settled_count = write_off_receivables(settled, SETTLED_REASON)
            overpayment_count = sum(
                reconcile_overpayment(receivable, OVERPAYMENT_REASON)
                for receivable in overpayments
            )
            if (
                cutoff_count,
                settled_count,
                overpayment_count,
            ) != APPROVED_WRITE_OFF_GROUPS:
                raise CommandError('Resultado das baixas diverge do plano aprovado.')
            payment_count, _payment_total, _write_off_count = APPROVED_SUMMARY
            if Payment.objects.count() != payment_count or FinancialMovement.objects.filter(
                source=FinancialMovement.Source.PAYMENT,
            ).count() != payment_count:
                raise CommandError('Resultado dos pagamentos diverge do plano aprovado.')

        self.stdout.write(self.style.SUCCESS(
            'Recuperação concluída: 110 pagamentos e 25.100 baixas auditadas.'
        ))
