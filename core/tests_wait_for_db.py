from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db.utils import OperationalError
from django.test import TestCase


class WaitForDbTests(TestCase):
    def test_succeeds_immediately_when_database_is_up(self):
        out = StringIO()
        call_command('wait_for_db', stdout=out)
        self.assertIn('disponível', out.getvalue())

    def test_raises_after_timeout_when_database_never_comes_up(self):
        out = StringIO()
        err = StringIO()
        with patch(
            'django.db.backends.base.base.BaseDatabaseWrapper.ensure_connection',
            side_effect=OperationalError('recusado'),
        ):
            with self.assertRaises(OperationalError):
                call_command(
                    'wait_for_db', '--timeout', '0', '--interval', '0',
                    stdout=out, stderr=err,
                )
        self.assertIn('indisponível', err.getvalue())
