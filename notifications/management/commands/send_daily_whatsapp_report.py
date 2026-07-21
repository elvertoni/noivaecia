"""Send Ana's daily operational summary over WhatsApp (Evolution API).

Dry-run friendly and idempotent per recipient: a configured send records an
AuditLog for its reference date and destination. The ``--if-due`` flag lets
the in-container scheduler call this command frequently; once the configured
time has passed, any recipient still pending is sent without duplicating the
ones that already succeeded.
"""

import re
from datetime import date as date_cls, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from company.models import Company
from core.models import AuditLog
from notifications import evolution
from notifications.services import build_daily_report

SENT_ACTION = 'whatsapp_daily_report'
FAILED_ACTION = 'whatsapp_send_failed'
FAILED_RETRY_DELAY = timedelta(minutes=5)


def _digits(value):
    return re.sub(r'\D', '', value or '')


def _configured_targets(value):
    targets = []
    normalized = (value or '').strip()
    if not normalized:
        return targets

    if re.search(r'[,;\n]', normalized):
        candidates = re.split(r'[,;\n]+', normalized)
    else:
        marked = re.sub(
            r'(?<!^)\s+(?=(?:\+?55\d{10,11}\b|\+?55[\s(]))',
            '\n',
            normalized,
        )
        # Be defensive with values saved before the textarea normalisation or
        # edited directly in the database: "5543... 5543..." should still be
        # treated as two recipients, while "(43) 99999-8888" must remain one.
        raw_tokens = marked.split()
        if '\n' in marked:
            candidates = marked.splitlines()
        elif len(raw_tokens) > 1 and all(_digits(token).startswith('55') for token in raw_tokens):
            candidates = raw_tokens
        else:
            candidates = [normalized]

    for raw in candidates:
        target = _digits(raw)
        if target and target.startswith('55') and 12 <= len(target) <= 13 and target not in targets:
            targets.append(target)
    return targets


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
                            help='Envia após o horário configurado (uso do agendador).')

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
        targets = [manual_target] if manual_target else _configured_targets(company.whatsapp_report_number)
        is_manual = bool(manual_target)
        dry_run = options['dry_run']
        if_due = options['if_due']

        # Before the configured time, bail out silently. Once due, keep
        # checking pending recipients so deploys and recipients added later
        # in the day cannot make the report miss its only one-minute window.
        if if_due:
            if not company.whatsapp_reports_enabled:
                return
            now = timezone.localtime()
            due = company.whatsapp_report_time
            if (now.hour, now.minute) < (due.hour, due.minute):
                return

        # Real configured send requires the feature on; --to and --dry-run bypass.
        if not dry_run and not is_manual and not company.whatsapp_reports_enabled:
            self.stdout.write(self.style.WARNING(
                'Relatório diário por WhatsApp desabilitado (Empresa).'
            ))
            return

        if not dry_run and not targets:
            raise CommandError('Nenhum número de destino configurado.')

        send_targets = targets
        if not options['force'] and not is_manual:
            sent_targets = self._sent_targets(on_date)
            send_targets = [target for target in targets if target not in sent_targets]
            if not send_targets:
                if not if_due:
                    self.stdout.write(self.style.WARNING(
                        f'Relatório de {on_date.isoformat()} já foi enviado '
                        'para todos os destinos configurados.'
                    ))
                return

        if if_due and not options['force']:
            recently_failed = self._recently_failed_targets(on_date)
            send_targets = [
                target for target in send_targets
                if target not in recently_failed
            ]
            if not send_targets:
                return

        text = build_daily_report(on_date)

        if dry_run:
            self.stdout.write(text)
            return

        message_ids = {}
        failures = {}
        for target in send_targets:
            try:
                message_ids[target] = evolution.send_text(target, text)
            except evolution.EvolutionError as exc:
                failures[target] = str(exc)

        if message_ids and not is_manual:
            self._record_sent(company, on_date, message_ids)

        if failures:
            AuditLog.record(
                user=None,
                action=FAILED_ACTION,
                obj=company,
                reason='; '.join(f'{target}: {error}' for target, error in failures.items()),
                metadata={
                    'reference_date': on_date.isoformat(),
                    'targets': list(failures.keys()),
                    'errors': failures,
                    'sent_targets': list(message_ids.keys()),
                },
            )
            raise CommandError(
                f'Falha no envio para {len(failures)} destino(s): '
                + ', '.join(failures.keys())
            )

        self.stdout.write(self.style.SUCCESS(
            f'Relatório de {on_date.isoformat()} enviado para {len(message_ids)} destino(s).'
        ))

    def _parse_date(self, raw):
        raw = (raw or '').strip()
        if not raw:
            return timezone.localdate()
        try:
            return date_cls.fromisoformat(raw)
        except ValueError:
            raise CommandError('--date deve estar no formato YYYY-MM-DD.')

    def _sent_targets(self, on_date):
        logs = AuditLog.objects.filter(
            action=SENT_ACTION,
            metadata__reference_date=on_date.isoformat(),
        ).only('metadata')
        sent_targets = set()
        for log in logs:
            metadata = log.metadata or {}
            targets = metadata.get('targets')
            if isinstance(targets, list):
                sent_targets.update(str(target) for target in targets if target)
            elif metadata.get('target'):
                sent_targets.add(str(metadata['target']))
        return sent_targets

    def _record_sent(self, company, on_date, message_ids):
        targets = list(message_ids.keys())
        metadata = {
            'reference_date': on_date.isoformat(),
            'targets': targets,
            'message_ids': {
                target: str(message_id)
                for target, message_id in message_ids.items()
            },
        }
        if len(targets) == 1:
            metadata['target'] = targets[0]
            metadata['message_id'] = str(message_ids[targets[0]])
        AuditLog.record(
            user=None,
            action=SENT_ACTION,
            obj=company,
            metadata=metadata,
        )

    def _recently_failed_targets(self, on_date):
        retry_after = timezone.now() - FAILED_RETRY_DELAY
        logs = AuditLog.objects.filter(
            action=FAILED_ACTION,
            metadata__reference_date=on_date.isoformat(),
            created_at__gte=retry_after,
        ).only('metadata')
        failed_targets = set()
        for log in logs:
            targets = (log.metadata or {}).get('targets', [])
            if isinstance(targets, list):
                failed_targets.update(
                    str(target) for target in targets if target
                )
        return failed_targets
