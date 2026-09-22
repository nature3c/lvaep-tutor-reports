from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from reports.models import Assignment, Goal, MonthLock, Session, StudentGoal
from reports.utils import fiscal_year_for, fy_months, is_locked
from .base import ReportTestCase


class SessionValidationTests(ReportTestCase):
    def candidate(self, **changes):
        values = dict(assignment=self.assignment, date=date(2026, 9, 15), hours=Decimal('1.50'), status='HELD', created_by=self.tutor)
        values.update(changes)
        return Session(**values)

    def test_held_bounds_and_quarter_steps(self):
        for hours in ['0.25', '1', '1.75', '8']:
            with self.subTest(hours=hours):
                self.candidate(hours=Decimal(hours)).full_clean()
        for hours in ['0', '-1', '0.24', '1.30', '8.25']:
            with self.subTest(hours=hours), self.assertRaises(ValidationError):
                self.candidate(hours=Decimal(hours)).full_clean()

    def test_absences_require_zero_hours(self):
        for status in ('TA', 'SA', 'H'):
            self.candidate(status=status, hours=Decimal('0')).full_clean()
            with self.assertRaises(ValidationError):
                self.candidate(status=status).full_clean()

    def test_db_rejects_invalid_status_and_hours_even_without_clean(self):
        for status, hours in [('HELD', '1.30'), ('HELD', '0'), ('HELD', '8.25'), ('TA', '1.5'), ('BAD', '0')]:
            with self.subTest(status=status, hours=hours), self.assertRaises(IntegrityError), transaction.atomic():
                Session.objects.bulk_create([self.candidate(status=status, hours=Decimal(hours))])

    def test_dates_and_stop_boundaries(self):
        for day in [date(2026, 9, 23), date(2026, 6, 30)]:
            with self.assertRaises(ValidationError):
                self.candidate(date=day).full_clean()
        self.candidate(date=self.assignment.start_date).full_clean()
        self.assignment.status = 'STOPPED'
        self.assignment.stopped_on = date(2026, 9, 16)
        self.assignment.stopped_reason = 'Relocated'
        self.assignment.save()
        self.candidate(date=date(2026, 9, 16)).full_clean()
        with self.assertRaises(ValidationError):
            self.candidate(date=date(2026, 9, 17)).full_clean()

    def test_duplicate_day_rejected_by_model_and_database(self):
        self.session()
        with self.assertRaises(ValidationError):
            self.candidate().save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Session.objects.bulk_create([self.candidate()])
        self.candidate(assignment=self.other_assignment, created_by=self.other).save()

    def test_only_one_active_pairing_but_stopped_history_allowed(self):
        with self.assertRaises(ValidationError):
            Assignment.objects.create(tutor=self.tutor, student=self.student, site=self.site, meeting_days='1', meeting_time='1pm', start_date=date(2026, 7, 1))
        self.assignment.status = 'STOPPED'
        self.assignment.stopped_on = date(2026, 9, 16)
        self.assignment.stopped_reason = 'Schedule changed'
        self.assignment.save()
        Assignment.objects.create(tutor=self.tutor, student=self.student, site=self.site, meeting_days='2', meeting_time='1pm', start_date=date(2026, 9, 17))

    def test_stop_cannot_invalidate_existing_sessions(self):
        self.session()
        self.assignment.status = 'STOPPED'
        self.assignment.stopped_on = date(2026, 9, 14)
        self.assignment.stopped_reason = 'Relocated'
        with self.assertRaises(ValidationError):
            self.assignment.save()


class MonthLockTests(ReportTestCase):
    def setUp(self):
        self.record = self.session()
        self.lock = MonthLock.objects.create(year=2026, month=9, locked_by=self.staff)
        self.client.force_login(self.tutor)

    def test_model_create_edit_move_and_delete_blocked(self):
        with self.assertRaises(ValidationError):
            self.session(day=date(2026, 9, 16))
        self.record.notes = 'Changed'
        with self.assertRaises(ValidationError):
            self.record.save()
        self.record.date = date(2026, 8, 15)
        with self.assertRaises(ValidationError):
            self.record.save()
        with self.assertRaises(ValidationError):
            self.record.delete()
        with self.assertRaises(ValidationError):
            Session.objects.filter(pk=self.record.pk).delete()
        self.assertTrue(is_locked(date(2026, 9, 1)))

    def test_tutor_cannot_create_edit_or_delete_locked_sessions(self):
        response = self.client.post(f'/assignments/{self.assignment.pk}/log/', {'date': '2026-09-16', 'status': 'HELD', 'hours': '1.5'})
        self.assertContains(response, 'locked', status_code=400)
        self.client.post(f'/sessions/{self.record.pk}/edit/', {'date': '2026-08-15', 'status': 'HELD', 'hours': '2', 'notes': 'Changed'})
        self.client.post(f'/sessions/{self.record.pk}/delete/')
        self.record.refresh_from_db()
        self.assertEqual(self.record.date, date(2026, 9, 15))
        self.assertEqual(self.record.hours, Decimal('1.5'))
        self.assertEqual(self.record.notes, '')

    def test_unlocked_session_cannot_move_into_locked_month(self):
        august = self.session(day=date(2026, 8, 15))
        self.client.post(f'/sessions/{august.pk}/edit/', {'date': '2026-09-10', 'status': 'HELD', 'hours': '2'})
        august.refresh_from_db()
        self.assertEqual(august.date, date(2026, 8, 15))

    def test_unlock_allows_changes(self):
        self.lock.delete()
        self.record.notes = 'Updated after reopening'
        self.record.save()
        self.record.delete()
        self.assertEqual(Session.objects.count(), 0)

    def test_locked_pairing_cannot_change_report_dimensions(self):
        self.assignment.tutor = self.other
        with self.assertRaises(ValidationError):
            self.assignment.save()


class FiscalYearTests(ReportTestCase):
    def test_boundaries_and_month_sequence(self):
        self.assertEqual(fiscal_year_for(date(2026, 6, 30)), 2025)
        self.assertEqual(fiscal_year_for(date(2026, 7, 1)), 2026)
        months = fy_months(2026)
        self.assertEqual(len(months), 12)
        self.assertEqual(months[0], (2026, 7))
        self.assertEqual(months[-1], (2027, 6))
        self.assertEqual(months[6], (2027, 1))

    def test_catalog_seed_is_exact(self):
        self.assertEqual(Goal.objects.count(), 17)
        self.assertEqual(list(Goal.objects.filter(is_reportable=True).values_list('text', flat=True)), [
            'Enter Employment', 'Retain Employment', 'Enter Occupational Skills Training Program',
            'Enter Postsecondary Education', 'Obtain High School Diploma', 'Obtain citizenship',
        ])
        self.assertFalse(Goal.objects.filter(category='E').exists())

    def test_goal_requires_catalog_or_custom_and_unique_catalog(self):
        goal = Goal.objects.first()
        StudentGoal.objects.create(student=self.student, goal=goal, attained_on=date(2026, 9, 1), recorded_by=self.tutor)
        with self.assertRaises(ValidationError):
            StudentGoal.objects.create(student=self.student, goal=goal, attained_on=date(2026, 9, 2), recorded_by=self.tutor)
        with self.assertRaises(ValidationError):
            StudentGoal.objects.create(student=self.student, recorded_by=self.tutor)
