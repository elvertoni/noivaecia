"""Send Ana's daily operational summary over WhatsApp (Evolution API).

Dry-run friendly and idempotent: a configured send records an AuditLog for
its reference date, so a container restart never sends the same day twice.
The ``--if-due`` flag lets the in-container scheduler call this every minute
cheaply — it returns in well under a second unless it is exactly the
configured minute and the report has not gone out yet.
"""

import re
from datetime import date as date_cls

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from company.models import Company
from core.models import AuditLog
from notifications import evolution
from notifications.services import build_daily_report

SENT_ACTION = 'whatsapp_daily_report'
FAILED_ACTION = 'whatsapp_send_failed'


def _digits(value):
    return re.sub(r'\D', '', value or '')


class Command(BaseCommand):
    help = 'Envia o relatório diário da Ana pelo WhatsApp (Evolution API).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Monta e imprime o texto, sem enviar.')
        parser.add_argument('--force', action='store_true',
                            help='Ignora a trava de reenvio do dia.')
        parser.add_argument('--to', default='',
                            help='Número de destino alternativo (só dígitos) para teste.')
        parser.add_argument('--check', action='store_true',
                            help='Só consulta o estado da conexão da instância.')
        parser.add_argument('--date', default='',
                            help='Data de referência YYYY-MM-DD (padrão: hoje).')
        parser.add_argument('--if-due', action='store_true',
                            help='Só envia se for o minuto configurado (uso do agendador).')

    def handle(self, *args, **options):
        company = Company.load()

        if options['check']:
            try:
                state = evolution.get_connection_state()
            except evolution.EvolutionError as exc:
                raise CommandError(str(exc))
            self.stdout.write(f'Estado da conexão: {state}')
            return

        on_date = self._parse_date(options['date'])
        manual_target = _digits(options['to'])
        target = manual_target or _digits(company.whatsapp_report_number)
        is_manual = bool(manual_target)
        dry_run = options['dry_run']
        if_due = options['if_due']

        # Scheduler path: bail out silently unless enabled and at the minute.
        if if_due:
            if not company.whatsapp_reports_enabled:
                return
            now = timezone.localtime()
            due = company.whatsapp_report_time
            if (now.hour, now.minute) != (due.hour, due.minute):
                return

        # Real configured send requires the feature on; --to and --dry-run bypass.
        if not dry_run and not is_manual and not company.whatsapp_reports_enabled:
            self.stdout.write(self.style.WARNING(
                'Relatório diário por WhatsApp desabilitado (Empresa).'
            ))
            return

        if not dry_run and not target:
            raise CommandError('Nenhum número de destino configurado.')

        # Idempotency: skip a configured send already done for this date.
        if not options['force'] and not is_manual and self._already_sent(on_date):
            if not if_due:
                self.stdout.write(self.style.WARNING(
                    f'Relatório de {on_date.isoformat()} já foi enviado.'
                ))
            return

        text = build_daily_report(on_date)

        if dry_run:
            self.stdout.write(text)
            return

        try:
            message_id = evolution.send_text(target, text)
        except evolution.EvolutionError as exc:
            AuditLog.record(
                user=None,
                action=FAILED_ACTION,
                obj=company,
                reason=str(exc),
                metadata={
                    'reference_date': on_date.isoformat(),
                    'target': target,
                },
            )
            raise CommandError(f'Falha no envio: {exc}')

        if not is_manual:
            AuditLog.record(
                user=None,
                action=SENT_ACTION,
                obj=company,
                metadata={
                    'reference_date': on_date.isoformat(),
                    'target': target,
                    'message_id': str(message_id),
                },
            )
        self.stdout.write(self.style.SUCCESS(
            f'Relatório de {on_date.isoformat()} enviado para {target}.'
        ))

    def _parse_date(self, raw):
        raw = (raw or '').strip()
        if not raw:
            return timezone.localdate()
        try:
            return date_cls.fromisoformat(raw)
        except ValueError:
            raise CommandError('--date deve estar no formato YYYY-MM-DD.')

    def _already_sent(self, on_date):
        return AuditLog.objects.filter(
            action=SENT_ACTION,
            metadata__reference_date=on_date.isoformat(),
        ).exists()
