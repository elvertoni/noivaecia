from datetime import datetime, time, timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from company.models import Company
from core.models import AuditLog
from notifications import evolution

CMD = 'notifications.management.commands.send_daily_whatsapp_report'


class SendDailyReportCommandTests(TestCase):
    def setUp(self):
        self.company = Company.load()
        self.company.whatsapp_reports_enabled = True
        self.company.whatsapp_report_number = '5543999998888'
        self.company.whatsapp_report_time = time(7, 30)
        self.company.save()

    def run_cmd(self, *args):
        out = StringIO()
        call_command('send_daily_whatsapp_report', *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    @mock.patch(f'{CMD}.evolution.send_text')
    def test_dry_run_does_not_send(self, send_text):
        out = self.run_cmd('--dry-run', '--date', '2026-07-20')
        send_text.assert_not_called()
        self.assertFalse(AuditLog.objects.exists())
        self.assertTrue(out.strip())

    @mock.patch(f'{CMD}.evolution.send_text')
    def test_disabled_feature_skips(self, send_text):
        self.company.whatsapp_reports_enabled = False
        self.company.save()
        out = self.run_cmd('--date', '2026-07-20')
        send_text.assert_not_called()
        self.assertIn('desabilitado', out)

    @mock.patch(f'{CMD}.evolution.send_text', return_value='MSGID1')
    def test_configured_send_records_audit(self, send_text):
        self.run_cmd('--date', '2026-07-20')
        send_text.assert_called_once()
        self.assertEqual(send_text.call_args.args[0], '5543999998888')
        log = AuditLog.objects.get(action='whatsapp_daily_report')
        self.assertEqual(log.metadata['reference_date'], '2026-07-20')
        self.assertEqual(log.metadata['message_id'], 'MSGID1')

    @mock.patch(f'{CMD}.evolution.send_text', side_effect=['MSGID1', 'MSGID2'])
    def test_configured_send_supports_multiple_targets(self, send_text):
        self.company.whatsapp_report_number = '5543999998888\n5543988887777'
        self.company.save()
        self.run_cmd('--date', '2026-07-20')
        self.assertEqual(
            [call.args[0] for call in send_text.call_args_list],
            ['5543999998888', '5543988887777'],
        )
        log = AuditLog.objects.get(action='whatsapp_daily_report')
        self.assertEqual(log.metadata['targets'], ['5543999998888', '5543988887777'])
        self.assertEqual(
            log.metadata['message_ids'],
            {'5543999998888': 'MSGID1', '5543988887777': 'MSGID2'},
        )

    @mock.patch(f'{CMD}.evolution.send_text', return_value='MSGID2')
    def test_new_target_added_after_daily_send_is_still_sent(self, send_text):
        AuditLog.objects.create(
            user=None, action='whatsapp_daily_report',
            model_name='Company', object_id='1',
            metadata={
                'reference_date': '2026-07-20',
                'target': '5543999998888',
                'message_id': 'MSGID1',
            },
        )
        self.company.whatsapp_report_number = '5543999998888\n5543988887777'
        self.company.save()

        self.run_cmd('--date', '2026-07-20')

        send_text.assert_called_once()
        self.assertEqual(send_text.call_args.args[0], '5543988887777')
        self.assertEqual(
            AuditLog.objects.filter(action='whatsapp_daily_report').count(),
            2,
        )

    @mock.patch(f'{CMD}.evolution.send_text')
    def test_idempotency_skips_only_when_all_targets_were_sent(self, send_text):
        AuditLog.objects.create(
            user=None, action='whatsapp_daily_report',
            model_name='Company', object_id='1',
            metadata={
                'reference_date': '2026-07-20',
                'targets': ['5543999998888', '5543988887777'],
                'message_ids': {
                    '5543999998888': 'MSGID1',
                    '5543988887777': 'MSGID2',
                },
            },
        )
        self.company.whatsapp_report_number = '5543999998888\n5543988887777'
        self.company.save()

        out = self.run_cmd('--date', '2026-07-20')

        send_text.assert_not_called()
        self.assertIn('todos os destinos configurados', out)

    @mock.patch(
        f'{CMD}.evolution.send_text',
        side_effect=['MSGID1', evolution.EvolutionError('boom')],
    )
    def test_partial_failure_records_success_and_failed_target(self, send_text):
        self.company.whatsapp_report_number = '5543999998888\n5543988887777'
        self.company.save()

        with self.assertRaises(CommandError):
            self.run_cmd('--date', '2026-07-20')

        self.assertEqual(send_text.call_count, 2)
        sent_log = AuditLog.objects.get(action='whatsapp_daily_report')
        self.assertEqual(sent_log.metadata['targets'], ['5543999998888'])
        failed_log = AuditLog.objects.get(action='whatsapp_send_failed')
        self.assertEqual(failed_log.metadata['targets'], ['5543988887777'])
        self.assertEqual(failed_log.metadata['sent_targets'], ['5543999998888'])

    @mock.patch(f'{CMD}.evolution.send_text', return_value='MSGID2')
    def test_retry_after_partial_failure_sends_only_failed_target(self, send_text):
        AuditLog.objects.create(
            user=None, action='whatsapp_daily_report',
            model_name='Company', object_id='1',
            metadata={
                'reference_date': '2026-07-20',
                'targets': ['5543999998888'],
                'message_ids': {'5543999998888': 'MSGID1'},
            },
        )
        AuditLog.objects.create(
            user=None, action='whatsapp_send_failed',
            model_name='Company', object_id='1',
            metadata={
                'reference_date': '2026-07-20',
                'targets': ['5543988887777'],
            },
        )
        self.company.whatsapp_report_number = '5543999998888\n5543988887777'
        self.company.save()

        self.run_cmd('--date', '2026-07-20')

        send_text.assert_called_once()
        self.assertEqual(send_text.call_args.args[0], '5543988887777')

    @mock.patch(f'{CMD}.evolution.send_text', side_effect=['MSGID1', 'MSGID2'])
    def test_configured_send_parses_space_separated_saved_numbers(self, send_text):
        self.company.whatsapp_report_number = '5543999998888 5543988887777'
        self.company.save()

        self.run_cmd('--date', '2026-07-20')

        self.assertEqual(
            [call.args[0] for call in send_text.call_args_list],
            ['5543999998888', '5543988887777'],
        )

    @mock.patch(f'{CMD}.evolution.send_text', side_effect=['MSGID1', 'MSGID2'])
    def test_configured_send_parses_formatted_numbers_on_same_line(self, send_text):
        self.company.whatsapp_report_number = '+55 (43) 99999-8888 +55 (43) 98888-7777'
        self.company.save()

        self.run_cmd('--date', '2026-07-20')

        self.assertEqual(
            [call.args[0] for call in send_text.call_args_list],
            ['5543999998888', '5543988887777'],
        )

    @mock.patch(f'{CMD}.evolution.send_text', return_value='MSGID1')
    def test_idempotent_second_run_skips(self, send_text):
        self.run_cmd('--date', '2026-07-20')
        self.run_cmd('--date', '2026-07-20')
        send_text.assert_called_once()
        self.assertEqual(
            AuditLog.objects.filter(action='whatsapp_daily_report').count(), 1
        )

    @mock.patch(f'{CMD}.evolution.send_text', return_value='MSGID2')
    def test_force_overrides_idempotency(self, send_text):
        self.run_cmd('--date', '2026-07-20')
        self.run_cmd('--date', '2026-07-20', '--force')
        self.assertEqual(send_text.call_count, 2)

    @mock.patch(f'{CMD}.evolution.send_text', return_value='X')
    def test_manual_to_bypasses_gate_and_idempotency(self, send_text):
        self.company.whatsapp_reports_enabled = False
        self.company.save()
        AuditLog.objects.create(
            user=None, action='whatsapp_daily_report',
            model_name='Company', object_id='1',
            metadata={'reference_date': '2026-07-20'},
        )
        self.run_cmd('--to', '(43) 98888-7777', '--date', '2026-07-20')
        send_text.assert_called_once()
        self.assertEqual(send_text.call_args.args[0], '43988887777')
        # manual send must not add a daily-sent audit row
        self.assertEqual(
            AuditLog.objects.filter(action='whatsapp_daily_report').count(), 1
        )

    @mock.patch(f'{CMD}.evolution.send_text', side_effect=evolution.EvolutionError('boom'))
    def test_send_failure_records_audit_and_raises(self, send_text):
        with self.assertRaises(CommandError):
            self.run_cmd('--date', '2026-07-20')
        self.assertTrue(AuditLog.objects.filter(action='whatsapp_send_failed').exists())
        self.assertFalse(AuditLog.objects.filter(action='whatsapp_daily_report').exists())

    @mock.patch(f'{CMD}.evolution.get_connection_state', return_value='open')
    def test_check_prints_state(self, state):
        out = self.run_cmd('--check')
        self.assertIn('open', out)

    @mock.patch(f'{CMD}.evolution.send_text', return_value='X')
    @mock.patch(f'{CMD}.timezone.localtime')
    def test_if_due_skips_before_configured_time(self, localtime, send_text):
        localtime.return_value = timezone.make_aware(datetime(2026, 7, 20, 7, 0))
        self.run_cmd('--if-due', '--date', '2026-07-20')
        send_text.assert_not_called()

    @mock.patch(f'{CMD}.evolution.send_text', return_value='X')
    @mock.patch(f'{CMD}.timezone.localtime')
    def test_if_due_sends_at_the_minute(self, localtime, send_text):
        localtime.return_value = timezone.make_aware(datetime(2026, 7, 20, 7, 30))
        self.run_cmd('--if-due', '--date', '2026-07-20')
        send_text.assert_called_once()

    @mock.patch(f'{CMD}.evolution.send_text', return_value='X')
    @mock.patch(f'{CMD}.timezone.localtime')
    def test_if_due_catches_up_after_configured_time(self, localtime, send_text):
        localtime.return_value = timezone.make_aware(datetime(2026, 7, 20, 9, 0))
        self.run_cmd('--if-due', '--date', '2026-07-20')
        send_text.assert_called_once()

    @mock.patch(f'{CMD}.evolution.send_text', return_value='MSGID2')
    @mock.patch(f'{CMD}.timezone.localtime')
    def test_if_due_sends_recipient_added_after_daily_send(
        self, localtime, send_text,
    ):
        localtime.return_value = timezone.make_aware(datetime(2026, 7, 20, 9, 0))
        AuditLog.objects.create(
            user=None,
            action='whatsapp_daily_report',
            model_name='Company',
            object_id='1',
            metadata={
                'reference_date': '2026-07-20',
                'targets': ['5543999998888'],
                'message_ids': {'5543999998888': 'MSGID1'},
            },
        )
        self.company.whatsapp_report_number = (
            '5543999998888\n5543988887777'
        )
        self.company.save()

        self.run_cmd('--if-due', '--date', '2026-07-20')

        send_text.assert_called_once_with('5543988887777', mock.ANY)

    @mock.patch(f'{CMD}.evolution.send_text')
    @mock.patch(f'{CMD}.timezone.localtime')
    def test_if_due_throttles_recent_failed_recipient(
        self, localtime, send_text,
    ):
        localtime.return_value = timezone.make_aware(datetime(2026, 7, 20, 9, 0))
        AuditLog.objects.create(
            user=None,
            action='whatsapp_send_failed',
            model_name='Company',
            object_id='1',
            metadata={
                'reference_date': '2026-07-20',
                'targets': ['5543999998888'],
            },
        )

        self.run_cmd('--if-due', '--date', '2026-07-20')

        send_text.assert_not_called()

    @mock.patch(f'{CMD}.evolution.send_text', return_value='MSGID1')
    @mock.patch(f'{CMD}.timezone.localtime')
    def test_if_due_retries_recipient_after_failure_delay(
        self, localtime, send_text,
    ):
        localtime.return_value = timezone.make_aware(datetime(2026, 7, 20, 9, 0))
        failure = AuditLog.objects.create(
            user=None,
            action='whatsapp_send_failed',
            model_name='Company',
            object_id='1',
            metadata={
                'reference_date': '2026-07-20',
                'targets': ['5543999998888'],
            },
        )
        AuditLog.objects.filter(pk=failure.pk).update(
            created_at=timezone.now() - timedelta(minutes=6)
        )

        self.run_cmd('--if-due', '--date', '2026-07-20')

        send_text.assert_called_once()
