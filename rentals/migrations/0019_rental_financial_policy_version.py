from datetime import timedelta

from django.db import migrations, models
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import TruncMinute


LEGACY_ACCESS = 0
ENFORCED_V1 = 1


def find_import_batch_minute(minute_counts):
    """Return the creation-minute with the most rentals, or ``None``.

    ``minute_counts`` maps a datetime truncated to the minute to how many
    rentals were created in that minute. The one-time Access import wrote
    tens of thousands of rows in a single batch, so its minute dwarfs any
    minute of ordinary, one-rental-at-a-time operator activity — that is
    what lets it be found without hardcoding the import's timestamp.
    Returns ``None`` when there is nothing to look at (e.g. an empty
    database), instead of raising on ``max()`` of an empty sequence.
    """
    if not minute_counts:
        return None
    return max(
        minute_counts.items(),
        key=lambda item: (item[1], item[0]),
    )[0]


def classify_financial_policy(*, has_legacy_mark, created_at, import_batch_minute):
    """Pure classification rule, mirrored as set-based SQL in the backfill below.

    A rental is LEGACY_ACCESS when it carries explicit evidence of having
    come from the Access import (a legacy note, or a linked receivable or
    payment tagged with a legacy source), or when it was created in the
    same one-minute window as the bulk import batch (rows the importer
    wrote that happen to carry none of those explicit marks on their own).
    Everything else is ENFORCED_V1 — including rentals created by real
    operators on the same calendar day as the import, since only the exact
    batch minute counts as import evidence.

    Unlike the old ``pk == number`` heuristic, this rule never depends on
    primary keys or rental numbers, so it cannot silently flip once IDs and
    numbers drift apart (e.g. after a deletion or a Company counter reset).
    """
    if has_legacy_mark:
        return LEGACY_ACCESS
    if (
        import_batch_minute is not None
        and created_at is not None
        and import_batch_minute <= created_at < import_batch_minute + timedelta(minutes=1)
    ):
        return LEGACY_ACCESS
    return ENFORCED_V1


def backfill_financial_policy_version(apps, schema_editor):
    """Classify every rental as LEGACY_ACCESS or ENFORCED_V1.

    Implements the ``classify_financial_policy`` predicate above as two
    set-based UPDATEs (no per-row Python loop), so it stays fast against
    the ~36k rentals in production.
    """
    Rental = apps.get_model('rentals', 'Rental')
    Receivable = apps.get_model('billing', 'Receivable')
    Payment = apps.get_model('billing', 'Payment')
    database = schema_editor.connection.alias

    has_legacy_receivable = Exists(
        Receivable.objects.using(database)
        .filter(rental_id=OuterRef('pk'))
        .filter(legacy_source__isnull=False)
        .exclude(legacy_source=''),
    )
    has_legacy_payment = Exists(
        Payment.objects.using(database).filter(
            rental_id=OuterRef('pk'),
            legacy_movement_id__isnull=False,
        ),
    )

    legacy_rentals = (
        Rental.objects.using(database)
        .annotate(
            has_legacy_receivable=has_legacy_receivable,
            has_legacy_payment=has_legacy_payment,
        )
        .filter(
            (Q(legacy_notes__isnull=False) & ~Q(legacy_notes=''))
            | Q(has_legacy_receivable=True)
            | Q(has_legacy_payment=True),
        )
    )
    explicit_legacy_pks = legacy_rentals.values('pk')

    minute_counts = dict(
        legacy_rentals
        .annotate(created_minute=TruncMinute('created_at'))
        .values('created_minute')
        .annotate(count=Count('pk'))
        .values_list('created_minute', 'count')
    )
    import_batch_minute = find_import_batch_minute(minute_counts)

    # legacy_q implements classify_financial_policy(...) above as a single
    # WHERE predicate: explicit marks, OR falling inside the derived import
    # batch minute. Keep the two definitions in sync — the pure function is
    # the one under test in rentals/tests_financial_policy_backfill.py.
    legacy_q = Q(pk__in=explicit_legacy_pks)
    if import_batch_minute is not None:
        legacy_q |= Q(
            created_at__gte=import_batch_minute,
            created_at__lt=import_batch_minute + timedelta(minutes=1),
        )

    Rental.objects.using(database).filter(legacy_q).update(
        financial_policy_version=LEGACY_ACCESS,
    )
    Rental.objects.using(database).exclude(legacy_q).update(
        financial_policy_version=ENFORCED_V1,
    )


# Production evidence for this rule (read-only measurement): there are 35,927
# rentals; id-number deltas are 0 for 35,887 and 1 for 40. The old heuristic
# cross-check found 35,745 true legacy matches, 142 false delta-0 matches, and
# no legacy matches among the 40 delta-1 rows. Of those 142 false matches, 130
# belong to the import batch at 2026-08-02 15:00 UTC, while 12 are real rentals
# created by operators on 2026-08-03 and 2026-08-04. The 130 batch rows have no
# explicit legacy mark; 59 have total_value=0 and 71 are nonzero. The code
# therefore derives the batch minute from the largest minute among explicitly
# marked legacy rentals, classifies every rental in that minute as LEGACY_ACCESS,
# and keeps the 12 later operator-created rentals ENFORCED_V1. It never uses
# those observed dates as a hardcoded cutoff.


class Migration(migrations.Migration):

    dependencies = [
        ('rentals', '0018_rental_cancellation_penalty_amount_and_more'),
        ('billing', '0002_cashaccount_financialmovement_payment_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='rental',
            name='financial_policy_version',
            field=models.PositiveSmallIntegerField(
                choices=[(0, 'Legado Access'), (1, 'Política financeira v1')],
                null=True,
                verbose_name='versão da política financeira',
            ),
        ),
        migrations.RunPython(
            backfill_financial_policy_version,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='rental',
            name='financial_policy_version',
            field=models.PositiveSmallIntegerField(
                choices=[(0, 'Legado Access'), (1, 'Política financeira v1')],
                default=1,
                verbose_name='versão da política financeira',
            ),
        ),
    ]
