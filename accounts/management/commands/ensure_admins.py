from django.conf import settings
from django.core.management.base import BaseCommand

from accounts.models import User


class Command(BaseCommand):
    help = 'Promote USER_CREATOR_EMAILS accounts to superuser/staff.'

    def handle(self, *args, **options):
        emails = getattr(settings, 'USER_CREATOR_EMAILS', [])
        if not emails:
            self.stdout.write(self.style.WARNING('USER_CREATOR_EMAILS está vazio.'))
            return
        for email in emails:
            updated = User.objects.filter(email=email).update(
                is_superuser=True,
                is_staff=True,
            )
            if updated:
                self.stdout.write(self.style.SUCCESS(f'Promovido: {email}'))
            else:
                self.stdout.write(self.style.WARNING(f'Não encontrado: {email}'))
