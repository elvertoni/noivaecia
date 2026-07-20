"""Merge duplicate customer records that share the same CPF.

Consumes a DBA-reviewed classification JSON (list of groups with ``cpf``,
``tier``, ``ids`` and ``winner_suggestion``) and merges each group in the
allowed tiers: rentals, payments and financial movements move to the winner,
empty contact fields on the winner are filled from the losers, and losers are
deactivated with an explanatory note — never deleted (``Rental.customer`` is
PROTECT by design). Every merge is wrapped in its own transaction and recorded
in AuditLog.

Groups outside ``--tiers`` or listed in ``--exclude-cpfs`` are skipped: those
require human review (see var/homologation dedupe classification report).

Dry-run by default; pass --apply to write.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from billing.models import FinancialMovement, Payment
from core.models import AuditLog
from customers.models import Customer
from rentals.models import Rental


DEFAULT_REASON = (
    'Merge de cadastros duplicados (mesmo CPF, mesma pessoa com grafias '
    'diferentes no legado Access), conforme classificação revisada.'
)
MERGED_MARKER = 'Mesclado no cliente #'

# Contact fields copied from losers when empty on the winner.
FILL_FIELDS = (
    'address', 'district', 'city', 'state', 'rg',
    'phone_home', 'phone_mobile', 'phone_work',
)


class Command(BaseCommand):
    help = 'Merge duplicate customers from a classification JSON; dry-run unless --apply.'

    def add_arguments(self, parser):
        parser.add_argument('json_path', help='Arquivo JSON de classificação dos grupos.')
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Grava as mesclagens. Sem esta opção, apenas exibe a prévia.',
        )
        parser.add_argument(
            '--tiers',
            default='T1,T2',
            help='Tiers permitidos, separados por vírgula (padrão: T1,T2).',
        )
        parser.add_argument(
            '--exclude-cpfs',
            default='',
            help='CPFs (só dígitos) a pular, separados por vírgula.',
        )
        parser.add_argument(
            '--reason',
            default=DEFAULT_REASON,
            help='Motivo gravado no log de auditoria.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Processa no máximo N grupos (0 = todos).',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        reason = (options['reason'] or '').strip()
        if not reason:
            raise CommandError('--reason não pode ficar vazio.')
        allowed_tiers = {t.strip() for t in options['tiers'].split(',') if t.strip()}
        excluded_cpfs = {c.strip() for c in options['exclude_cpfs'].split(',') if c.strip()}

        json_path = Path(options['json_path'])
        if not json_path.exists():
            raise CommandError(f'Arquivo não encontrado: {json_path}')
        groups = json.loads(json_path.read_text(encoding='utf-8'))

        mode = 'APLICANDO' if apply_changes else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(f'merge_duplicate_customers — {mode}'))

        in_scope = [
            g for g in groups
            if g['tier'] in allowed_tiers and g['cpf'] not in excluded_cpfs
        ]
        skipped_out_of_scope = len(groups) - len(in_scope)
        if options['limit']:
            in_scope = in_scope[:options['limit']]

        self.stdout.write(
            f'Grupos no escopo: {len(in_scope)} '
            f'(fora do escopo/tier/exclusão: {skipped_out_of_scope})'
        )

        merged = skipped_invalid = already_done = 0
        total_rentals = total_payments = total_movements = 0

        for group in in_scope:
            cpf = group['cpf']
            ids = [entry['id'] for entry in group['ids']]
            winner_id = group['winner_suggestion']
            customers = {c.pk: c for c in Customer.objects.filter(pk__in=ids)}

            problems = []
            if winner_id not in customers:
                problems.append(f'vencedor {winner_id} inexistente')
            missing = [pk for pk in ids if pk not in customers]
            if missing:
                problems.append(f'ids inexistentes: {missing}')
            wrong_cpf = [
                pk for pk, c in customers.items() if c.cpf_digits != cpf
            ]
            if wrong_cpf:
                problems.append(f'cpf_digits divergente do JSON: {wrong_cpf}')
            if problems:
                skipped_invalid += 1
                self.stdout.write(self.style.WARNING(
                    f'  CPF {cpf}: pulado ({"; ".join(problems)})'
                ))
                continue

            losers = [
                customers[pk] for pk in ids
                if pk != winner_id and not (
                    not customers[pk].is_active
                    and MERGED_MARKER in customers[pk].legacy_notes
                )
            ]
            if not losers:
                already_done += 1
                continue

            loser_ids = [l.pk for l in losers]
            rentals_n = Rental.objects.filter(customer_id__in=loser_ids).count()
            payments_n = Payment.objects.filter(customer_id__in=loser_ids).count()
            movements_n = FinancialMovement.objects.filter(customer_id__in=loser_ids).count()
            self.stdout.write(
                f'  CPF {cpf} [{group["tier"]}]: vencedor #{winner_id}, '
                f'perdedores {loser_ids} — {rentals_n} locações, '
                f'{payments_n} pagamentos, {movements_n} movimentos'
            )
            total_rentals += rentals_n
            total_payments += payments_n
            total_movements += movements_n

            if not apply_changes:
                merged += 1
                continue

            with transaction.atomic():
                winner = Customer.objects.select_for_update().get(pk=winner_id)
                locked_losers = list(
                    Customer.objects.select_for_update().filter(pk__in=loser_ids)
                )
                today = timezone.now().date().isoformat()

                for loser in locked_losers:
                    for field in FILL_FIELDS:
                        if not getattr(winner, field) and getattr(loser, field):
                            setattr(winner, field, getattr(loser, field))
                    if loser.notes:
                        winner.notes = (
                            f'{winner.notes}\n[do cadastro #{loser.pk}] {loser.notes}'
                        ).strip()
                    Rental.objects.filter(customer=loser).update(customer=winner)
                    Payment.objects.filter(customer=loser).update(customer=winner)
                    FinancialMovement.objects.filter(customer=loser).update(customer=winner)
                    loser.is_active = False
                    loser.legacy_notes = (
                        f'{loser.legacy_notes}\n{MERGED_MARKER}{winner.pk} em {today} '
                        f'(CPF duplicado, mesma pessoa).'
                    ).strip()
                    loser.save()

                winner.legacy_notes = (
                    f'{winner.legacy_notes}\nAbsorveu cadastro(s) '
                    f'#{", #".join(str(pk) for pk in loser_ids)} em {today} '
                    f'via merge de CPF duplicado.'
                ).strip()
                winner.save()

                AuditLog.record(
                    user=None,
                    action='merge_duplicate_customer',
                    obj=winner,
                    reason=reason,
                    metadata={
                        'cpf_digits': cpf,
                        'tier': group['tier'],
                        'winner_id': winner.pk,
                        'loser_ids': loser_ids,
                        'rentals_moved': rentals_n,
                        'payments_moved': payments_n,
                        'movements_moved': movements_n,
                    },
                )
            merged += 1

        summary = (
            f'Grupos processados: {merged} · já mesclados: {already_done} · '
            f'inválidos pulados: {skipped_invalid} · '
            f'locações: {total_rentals} · pagamentos: {total_payments} · '
            f'movimentos: {total_movements}'
        )
        if apply_changes:
            self.stdout.write(self.style.SUCCESS(summary))
        else:
            self.stdout.write(self.style.WARNING(
                f'{summary}\nDry-run concluído; use --apply para gravar.'
            ))
