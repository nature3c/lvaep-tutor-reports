from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command

from reports.models import Assignment, Goal, MonthLock, Session, Site, Student, StudentGoal
from .base import ReportTestCase


class DemoCommandTests(ReportTestCase):
    def test_existing_students_are_untouched_without_reset(self):
        out = StringIO()
        call_command('seed_demo', stdout=out)
        self.assertIn('left unchanged', out.getvalue())
        self.assertEqual(Student.objects.count(), 2)
        self.assertFalse(get_user_model().objects.filter(username='tutor1').exists())

    def test_reset_creates_valid_repeatable_demo(self):
        call_command('seed_demo', reset=True, stdout=StringIO())
        self.assertEqual(Site.objects.count(), 3)
        self.assertEqual(Student.objects.count(), 15)
        self.assertEqual(Assignment.objects.count(), 16)
        self.assertEqual(Assignment.objects.filter(status='STOPPED').count(), 1)
        self.assertTrue(MonthLock.objects.filter(year=2026, month=7).exists())
        self.assertEqual(Goal.objects.count(), 17)
        self.assertTrue(get_user_model().objects.get(username='tutor1').check_password('demo1234'))
        self.assertTrue(Session.objects.filter(status='H', date='2026-07-04').exists())
        self.assertTrue(StudentGoal.objects.exists())
        before = list(Session.objects.order_by('assignment_id', 'date').values_list('date', 'status', 'hours', 'notes'))
        count = len(before)
        call_command('seed_demo', stdout=StringIO())
        self.assertEqual(Session.objects.count(), count)
        call_command('seed_demo', reset=True, stdout=StringIO())
        after = list(Session.objects.order_by('assignment_id', 'date').values_list('date', 'status', 'hours', 'notes'))
        self.assertEqual(before, after)
        self.client.force_login(get_user_model().objects.get(username='tutor1'))
        self.assertTrue(any(card['prompts'] for card in self.client.get('/').context['cards']))
        self.client.force_login(get_user_model().objects.get(username='staff'))
        self.assertTrue(self.client.get('/staff/reports/?month=2026-09').context['missing'])
