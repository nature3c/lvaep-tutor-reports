import csv
import io
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import Client, override_settings

from reports.models import Assignment, Goal, MonthLock, Session, StudentGoal
from reports.utils import fiscal_grid
from .base import ReportTestCase


class AccessTests(ReportTestCase):
    def test_all_pages_require_login(self):
        record = self.session()
        for path in ['/', f'/assignments/{self.assignment.pk}/', f'/sessions/{record.pk}/edit/', f'/sessions/{record.pk}/delete/', f'/assignments/{self.assignment.pk}/goals/', f'/assignments/{self.assignment.pk}/stop/', '/staff/reports/', '/staff/reports/export/', '/staff/assignments/', f'/staff/assignments/{self.assignment.pk}/print/', '/admin/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 302)

    def test_cross_tutor_ids_are_404_on_get_and_post(self):
        self.client.force_login(self.tutor)
        record = self.session(assignment=self.other_assignment)
        for path in [f'/assignments/{self.other_assignment.pk}/', f'/sessions/{record.pk}/edit/', f'/sessions/{record.pk}/delete/', f'/assignments/{self.other_assignment.pk}/goals/', f'/assignments/{self.other_assignment.pk}/stop/']:
            for method in (self.client.get, self.client.post):
                with self.subTest(path=path, method=method.__name__):
                    self.assertEqual(method(path).status_code, 404)
        self.assertEqual(self.client.post(f'/assignments/{self.other_assignment.pk}/log/', {}).status_code, 404)
        self.assertContains(self.client.get('/'), 'Ana García')
        self.assertNotContains(self.client.get('/'), 'Mei Lin')

    def test_tutor_cannot_use_staff_endpoints(self):
        self.client.force_login(self.tutor)
        for path in ['/staff/reports/', '/staff/reports/export/', '/staff/assignments/', f'/staff/assignments/{self.assignment.pk}/print/']:
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post('/staff/reports/lock/?month=2026-09', {'action': 'lock'}).status_code, 403)
        self.assertFalse(MonthLock.objects.exists())

    def test_staff_can_see_all_assignments_and_admin_models(self):
        self.client.force_login(self.staff)
        for path in [f'/assignments/{self.other_assignment.pk}/', '/staff/reports/', '/staff/assignments/', f'/staff/assignments/{self.other_assignment.pk}/print/', '/admin/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        for model in ['site', 'student', 'assignment', 'session', 'goal', 'studentgoal', 'monthlock']:
            self.assertEqual(self.client.get(f'/admin/reports/{model}/').status_code, 200)

    def test_staff_without_own_assignments_redirects_to_report(self):
        self.client.force_login(self.staff)
        self.assertRedirects(self.client.get('/'), '/staff/reports/')

    def test_dashboard_only_shows_own_active_and_stopped_assignments(self):
        for user in (self.tutor, self.staff):
            self.assignment.tutor = user
            self.assignment.save()
            self.client.force_login(user)
            for status in ('ACTIVE', 'STOPPED'):
                with self.subTest(user=user.username, status=status):
                    for assignment in (self.assignment, self.other_assignment):
                        assignment.status = status
                        assignment.stopped_on = date(2026, 9, 15) if status == 'STOPPED' else None
                        assignment.stopped_reason = 'Moved' if status == 'STOPPED' else ''
                        assignment.save()
                    response = self.client.get('/')
                    self.assertContains(response, 'Ana García')
                    self.assertNotContains(response, 'Mei Lin')
                    self.assertNotContains(response, f'/assignments/{self.other_assignment.pk}/log/')
                    if status == 'ACTIVE':
                        self.assertEqual([card['assignment'] for card in response.context['cards']], [self.assignment])
                        self.assertTrue(response.context['cards'][0]['prompts'])
                        self.assertContains(response, f'/assignments/{self.assignment.pk}/log/')
                        self.assertEqual(list(response.context['stopped_assignments']), [])
                    else:
                        self.assertEqual(response.context['cards'], [])
                        self.assertEqual(list(response.context['stopped_assignments']), [self.assignment])

    def test_post_actions_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.tutor)
        self.assertEqual(client.post(f'/assignments/{self.assignment.pk}/log/', {'date': '2026-09-15', 'hours': '1.5', 'status': 'HELD'}).status_code, 403)

    @override_settings(DEMO_MODE=True)
    def test_login_displays_demo_credentials_only_in_demo_mode(self):
        self.assertContains(self.client.get('/accounts/login/'), 'demo1234')
        with override_settings(DEMO_MODE=False):
            self.assertNotContains(self.client.get('/accounts/login/'), 'demo1234')


class TutorWorkflowTests(ReportTestCase):
    def setUp(self):
        self.client.force_login(self.tutor)

    def test_dashboard_prompts_exclude_logged_days_and_locks(self):
        self.session(day=date(2026, 9, 15), hours='2')
        response = self.client.get('/')
        card = response.context['cards'][0]
        self.assertEqual(card['hours'], Decimal('2'))
        self.assertEqual(card['assignment'].month_hours, Decimal('2'))
        self.assertIn(date(2026, 9, 17), card['prompts'])
        self.assertNotIn(date(2026, 9, 15), card['prompts'])
        self.assertNotIn(date(2026, 9, 16), card['prompts'])
        self.assertTrue(all(date(2026, 9, 9) <= d <= date(2026, 9, 22) for d in card['prompts']))
        MonthLock.objects.create(year=2026, month=9, locked_by=self.staff)
        self.assertEqual(self.client.get('/').context['cards'][0]['prompts'], [])

    def test_prompts_never_precede_start_date(self):
        self.assignment.start_date = date(2026, 9, 18)
        self.assignment.save()
        prompts = self.client.get('/').context['cards'][0]['prompts']
        self.assertEqual(prompts, [date(2026, 9, 22)])

    def test_quick_log_defaults_and_absence_one_tap(self):
        card = self.client.get('/').context['cards'][0]
        self.assertEqual(card['hours'], Decimal('1.5'))
        prefix = f'a{self.assignment.pk}'
        response = self.client.post(f'/assignments/{self.assignment.pk}/log/', {
            'quick_log': '1', f'{prefix}-date': '2026-09-15', f'{prefix}-status': 'HELD', f'{prefix}-hours': '1.75', f'{prefix}-notes': 'Reading',
        })
        self.assertRedirects(response, '/')
        self.assertEqual(Session.objects.get().created_by, self.tutor)
        response = self.client.post(f'/assignments/{self.assignment.pk}/log/', {'date': '2026-09-17', 'status': 'SA', 'hours': '1.75'})
        self.assertRedirects(response, '/')
        self.assertEqual(Session.objects.get(date=date(2026, 9, 17)).hours, 0)
        self.assertEqual(self.client.get('/').context['cards'][0]['hours'], Decimal('1.75'))

    def test_invalid_quick_log_retains_inline_errors(self):
        prefix = f'a{self.assignment.pk}'
        response = self.client.post(f'/assignments/{self.assignment.pk}/log/', {
            'quick_log': '1', f'{prefix}-date': '2026-09-15', f'{prefix}-status': 'HELD', f'{prefix}-hours': '1.3',
        })
        self.assertContains(response, 'quarter-hour', status_code=400)
        self.assertEqual(Session.objects.count(), 0)

    def test_edit_and_delete_confirmation(self):
        record = self.session()
        response = self.client.post(f'/sessions/{record.pk}/edit/', {'date': '2026-09-15', 'status': 'HELD', 'hours': '2', 'notes': 'Updated'})
        self.assertRedirects(response, f'/assignments/{self.assignment.pk}/')
        record.refresh_from_db()
        self.assertEqual(record.hours, 2)
        self.assertContains(self.client.get(f'/sessions/{record.pk}/delete/'), 'Yes, delete session')
        self.assertTrue(Session.objects.filter(pk=record.pk).exists())
        self.client.post(f'/sessions/{record.pk}/delete/')
        self.assertFalse(Session.objects.filter(pk=record.pk).exists())

    def test_fiscal_grid_invalid_dates_absences_totals_and_selector(self):
        self.session(day=date(2026, 7, 2), hours='2')
        self.session(day=date(2026, 9, 15), status='TA', hours='0')
        context = fiscal_grid(self.assignment, 2026)
        self.assertEqual(context['fy_total'], 2)
        self.assertEqual(context['grid_months'][0]['total'], 2)
        self.assertFalse(context['grid_rows'][29]['cells'][7]['valid'])  # February 30
        self.assertFalse(context['grid_rows'][30]['cells'][2]['valid'])  # September 31
        self.assertEqual(context['grid_rows'][14]['cells'][2]['session'].status, 'TA')
        self.assertEqual(self.client.get(f'/assignments/{self.assignment.pk}/?fy=2025').context['fy_total'], 0)
        self.assertEqual(self.client.get(f'/assignments/{self.assignment.pk}/?fy=bad').status_code, 400)

    def test_stop_requires_reason_and_sends_email_once(self):
        url = f'/assignments/{self.assignment.pk}/stop/'
        self.client.post(url, {'stopped_on': '2026-09-20', 'stopped_reason': ''})
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.status, 'ACTIVE')
        self.assertEqual(len(mail.outbox), 0)
        response = self.client.post(url, {'stopped_on': '2026-09-20', 'stopped_reason': 'New work schedule'})
        self.assertRedirects(response, f'/assignments/{self.assignment.pk}/')
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.status, 'STOPPED')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('office@example.com', mail.outbox[0].to)
        self.assertIn('New work schedule', mail.outbox[0].body)
        self.client.post(url, {'stopped_on': '2026-09-20', 'stopped_reason': 'Again'})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.client.get('/').context['cards'], [])

    def test_check_uncheck_and_custom_achievements(self):
        goal = Goal.objects.first()
        url = f'/assignments/{self.assignment.pk}/goals/'
        self.client.post(url, {'action': 'checklist', f'goal_{goal.pk}': 'on', f'date_{goal.pk}': '2026-09-10'})
        achievement = StudentGoal.objects.get()
        self.assertEqual(achievement.recorded_by, self.tutor)
        self.assertEqual(achievement.attained_on, date(2026, 9, 10))
        self.client.post(url, {'action': 'checklist'})
        self.assertFalse(StudentGoal.objects.exists())
        self.client.post(url, {'action': 'other', 'custom_text': 'Read a novel', 'attained_on': '2026-09-11'})
        achievement = StudentGoal.objects.get()
        self.assertEqual(achievement.custom_text, 'Read a novel')
        self.assertIsNone(achievement.goal)
        self.client.post(url, {'action': 'remove_other', 'achievement_id': achievement.pk})
        self.assertFalse(StudentGoal.objects.exists())

    def test_locked_achievements_cannot_change_or_be_removed(self):
        goal = Goal.objects.first()
        record = StudentGoal.objects.create(student=self.student, goal=goal, attained_on=date(2026, 9, 10), recorded_by=self.tutor)
        other = StudentGoal.objects.create(student=self.student, custom_text='Read a novel', attained_on=date(2026, 9, 10), recorded_by=self.tutor)
        MonthLock.objects.create(year=2026, month=9, locked_by=self.staff)
        url = f'/assignments/{self.assignment.pk}/goals/'
        self.client.post(url, {'action': 'checklist'})
        self.client.post(url, {'action': 'remove_other', 'achievement_id': other.pk})
        self.assertEqual(StudentGoal.objects.count(), 2)
        record.refresh_from_db()
        self.assertEqual(record.attained_on, date(2026, 9, 10))

    def test_custom_goal_id_scoped_to_student(self):
        record = StudentGoal.objects.create(student=self.other_student, custom_text='Read a novel', attained_on=date(2026, 9, 10), recorded_by=self.other)
        response = self.client.post(f'/assignments/{self.assignment.pk}/goals/', {'action': 'remove_other', 'achievement_id': record.pk})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(StudentGoal.objects.filter(pk=record.pk).exists())


class StaffReportTests(ReportTestCase):
    def setUp(self):
        self.client.force_login(self.staff)

    def test_totals_grouping_and_missing_reports(self):
        self.session(day=date(2026, 9, 1), hours='1.5')
        self.session(day=date(2026, 9, 3), hours='2')
        self.session(day=date(2026, 9, 8), status='SA', hours='0')
        self.session(day=date(2026, 9, 10), status='TA', hours='0')
        self.session(day=date(2026, 9, 15), status='H', hours='0')
        self.session(day=date(2026, 8, 31), assignment=self.other_assignment, hours='3')
        response = self.client.get('/staff/reports/?month=2026-09')
        self.assertEqual(response.context['summary'], {'hours': Decimal('3.5'), 'held': 2, 'students': 1, 'ta': 1, 'sa': 1, 'holiday': 1})
        self.assertEqual(list(response.context['missing']), [self.other_assignment])
        for table in response.context['tables']:
            self.assertEqual(len(table['rows']), 1)
            row = table['rows'][0]
            self.assertEqual(row['hours'], Decimal('3.5'))
            self.assertEqual(row['held'], 2)
            self.assertEqual(row['assignments'], [self.assignment])
        self.assertContains(response, f'/staff/assignments/{self.assignment.pk}/print/')

    def test_absence_counts_as_a_submitted_report_but_not_student_served(self):
        self.session(status='TA', hours='0', assignment=self.other_assignment)
        response = self.client.get('/staff/reports/?month=2026-09')
        self.assertEqual(list(response.context['missing']), [self.assignment])
        self.assertEqual(response.context['summary']['students'], 0)

    def test_missing_excludes_future_but_includes_stopped_during_month(self):
        self.assignment.start_date = date(2026, 10, 1)
        self.assignment.save()
        self.other_assignment.status = 'STOPPED'
        self.other_assignment.stopped_on = date(2026, 9, 10)
        self.other_assignment.stopped_reason = 'Moved'
        self.other_assignment.save()
        response = self.client.get('/staff/reports/?month=2026-09')
        self.assertEqual(list(response.context['missing']), [self.other_assignment])
        self.assertEqual(list(response.context['stopped']), [self.other_assignment])
        self.assertContains(response, 'Moved')

    def test_stopping_pairing_preserves_historical_missing_report(self):
        url = '/staff/reports/?month=2026-08'
        self.session(day=date(2026, 8, 15), assignment=self.other_assignment)
        self.assertEqual(list(self.client.get(url).context['missing']), [self.assignment])
        self.client.post(f'/assignments/{self.assignment.pk}/stop/', {
            'stopped_on': '2026-09-15', 'stopped_reason': 'Moved',
        })
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.status, 'STOPPED')
        self.assertEqual(list(self.client.get(url).context['missing']), [self.assignment])
        self.session(day=date(2026, 8, 20), status='TA', hours='0')
        self.assertEqual(list(self.client.get(url).context['missing']), [])

    def test_missing_respects_month_boundaries(self):
        self.session(day=date(2026, 8, 15), assignment=self.other_assignment)
        for start, stopped, missing in [
            (date(2026, 8, 31), None, True),
            (date(2026, 9, 1), None, False),
            (date(2026, 7, 1), date(2026, 7, 31), False),
            (date(2026, 7, 1), date(2026, 8, 1), True),
        ]:
            with self.subTest(start=start, stopped=stopped):
                self.assignment.start_date = start
                self.assignment.status = 'STOPPED' if stopped else 'ACTIVE'
                self.assignment.stopped_on = stopped
                self.assignment.stopped_reason = 'Moved' if stopped else ''
                self.assignment.save()
                response = self.client.get('/staff/reports/?month=2026-08')
                self.assertEqual(list(response.context['missing']), [self.assignment] if missing else [])

    def test_distinct_students_across_pairings(self):
        extra = Assignment.objects.create(tutor=self.other, student=self.student, site=self.site, meeting_days='0', meeting_time='1pm', start_date=date(2026, 7, 1))
        self.session()
        self.session(assignment=extra)
        report = self.client.get('/staff/reports/?month=2026-09')
        self.assertEqual(report.context['summary']['students'], 1)
        self.assertEqual(report.context['summary']['held'], 2)
        for assignment in (self.assignment, extra):
            url = f'/staff/assignments/{assignment.pk}/print/?fy=2026'
            self.assertContains(report, f'<a class="table-link" href="{url}">Ana García · {assignment.tutor.get_full_name()}</a>', count=2, html=True)
            self.assertContains(report, f'<a class="table-link" href="{url}">Ana García</a>', count=1, html=True)

    def test_month_default_previous_for_first_seven_days(self):
        with patch('django.utils.timezone.localdate', return_value=date(2026, 9, 7)):
            self.assertEqual(self.client.get('/staff/reports/').context['month_value'], '2026-08')
        with patch('django.utils.timezone.localdate', return_value=date(2026, 1, 1)):
            self.assertEqual(self.client.get('/staff/reports/').context['month_value'], '2025-12')
        self.assertEqual(self.client.get('/staff/reports/').context['month_value'], '2026-09')

    def test_invalid_filters_are_bad_requests(self):
        for value in ['garbage', '2026-13', '2026-9', '0000-01']:
            for path in ['/staff/reports/', '/staff/reports/export/']:
                self.assertEqual(self.client.get(path, {'month': value}).status_code, 400)
        self.assertEqual(self.client.get('/staff/assignments/?site=bad').status_code, 400)

    def test_csv_headers_rows_and_escaping(self):
        self.session(notes='Reading, writing\nand speaking')
        self.session(day=date(2026, 9, 17), status='SA', hours='0', notes='=HYPERLINK("bad")')
        self.session(day=date(2026, 8, 1))
        response = self.client.get('/staff/reports/export/?month=2026-09')
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('tutor-sessions-2026-09.csv', response['Content-Disposition'])
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(rows[0], ['date', 'site', 'tutor', 'student', 'status', 'hours', 'notes'])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1], ['2026-09-15', 'Community Library', 'Alice Rivera', 'Ana García', 'HELD', '1.50', 'Reading, writing\nand speaking'])
        self.assertEqual(rows[2][4:6], ['SA', '0.00'])
        self.assertTrue(rows[2][6].startswith("'="))

    def test_lock_unlock_post_only(self):
        self.assertEqual(self.client.get('/staff/reports/lock/?month=2026-09').status_code, 405)
        self.client.post('/staff/reports/lock/?month=2026-09', {'action': 'lock'})
        self.assertEqual(MonthLock.objects.get().locked_by, self.staff)
        self.client.post('/staff/reports/lock/?month=2026-09', {'action': 'lock'})
        self.assertEqual(MonthLock.objects.count(), 1)
        self.client.post('/staff/reports/lock/?month=2026-09', {'action': 'unlock'})
        self.assertFalse(MonthLock.objects.exists())

    def test_assignment_filters_and_printable_fiscal_goal_scope(self):
        response = self.client.get('/staff/assignments/', {'q': 'Alice Rivera', 'site': self.site.pk, 'status': 'ACTIVE'})
        self.assertEqual(list(response.context['assignments']), [self.assignment])
        StudentGoal.objects.create(student=self.student, goal=Goal.objects.first(), attained_on=date(2026, 8, 1), recorded_by=self.tutor)
        StudentGoal.objects.create(student=self.student, custom_text='Previous fiscal year', attained_on=date(2026, 6, 1), recorded_by=self.tutor)
        response = self.client.get(f'/staff/assignments/{self.assignment.pk}/print/?fy=2026')
        self.assertContains(response, 'Student Monthly Attendance')
        self.assertContains(response, '08/01/2026')
        self.assertNotContains(response, 'Previous fiscal year')
        self.assertEqual(self.client.get(f'/staff/assignments/{self.assignment.pk}/print/?fy=wrong').status_code, 400)
