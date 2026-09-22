from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from reports.models import Assignment, Session, Site, Student


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ReportTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.enterClassContext(patch('django.utils.timezone.localdate', return_value=date(2026, 9, 22)))

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.tutor = User.objects.create_user('alice', password='test-pass-123', first_name='Alice', last_name='Rivera')
        cls.other = User.objects.create_user('bob', password='test-pass-123', first_name='Bob', last_name='Chen')
        cls.staff = User.objects.create_user('staff', password='test-pass-123', is_staff=True, is_superuser=True, email='office@example.com')
        cls.site = Site.objects.create(name='Community Library')
        cls.student = Student.objects.create(first_name='Ana', last_name='García')
        cls.other_student = Student.objects.create(first_name='Mei', last_name='Lin')
        cls.assignment = Assignment.objects.create(tutor=cls.tutor, student=cls.student, site=cls.site, meeting_days='1,3', meeting_time='6–7:30pm', start_date=date(2026, 7, 1))
        cls.other_assignment = Assignment.objects.create(tutor=cls.other, student=cls.other_student, site=cls.site, meeting_days='0,2', meeting_time='10–11am', start_date=date(2026, 7, 1))

    def session(self, day=date(2026, 9, 15), hours='1.50', status='HELD', assignment=None, **kwargs):
        assignment = assignment or self.assignment
        return Session.objects.create(assignment=assignment, date=day, hours=Decimal(hours), status=status, created_by=assignment.tutor, **kwargs)
