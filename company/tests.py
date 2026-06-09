from django.test import TestCase

from company.models import Company


class CompanySingletonTests(TestCase):
    def test_load_creates_single_row(self):
        a = Company.load()
        b = Company.load()
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(Company.objects.count(), 1)

    def test_save_forces_single_row(self):
        Company.load()
        Company(name='Outra').save()
        self.assertEqual(Company.objects.count(), 1)
        self.assertEqual(Company.load().name, 'Outra')

    def test_next_rental_number_is_sequential(self):
        nums = [Company.next_rental_number() for _ in range(3)]
        self.assertEqual(nums, [1, 2, 3])
        self.assertEqual(Company.load().last_rental_number, 3)

    def test_timestamps_present(self):
        company = Company.load()
        self.assertIsNotNone(company.created_at)
        self.assertIsNotNone(company.updated_at)
