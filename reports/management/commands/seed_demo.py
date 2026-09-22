import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from reports.models import Assignment, Goal, MonthLock, Session, Site, Student, StudentGoal


class Command(BaseCommand):
    help = 'Create a repeatable public demo. Does nothing if students exist; --reset replaces reporting data.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Delete existing reporting data and recreate the demo.')

    @transaction.atomic
    def handle(self, *args, **options):
        if Student.objects.exists() and not options['reset']:
            self.stdout.write('Students already exist; demo data left unchanged. Use --reset to replace it.')
            return
        if options['reset']:
            MonthLock.objects.all().delete()
            StudentGoal.objects.all().delete()
            Session.objects.all().delete()
            Assignment.objects.all().delete()
            Student.objects.all().delete()
            Site.objects.all().delete()
        rng = random.Random(20260701)
        today = timezone.localdate()
        start = date(2026, 7, 1)
        if today < start:
            self.stdout.write(self.style.WARNING('The demo starts July 1, 2026; session history will appear after that date.'))
        User = get_user_model()
        staff, _ = User.objects.get_or_create(username='staff')
        staff.first_name, staff.last_name = 'Morgan', 'Reed'
        staff.email = 'staff@example.com'
        # Not a superuser: demo credentials are public, so staff must not be able to edit user accounts.
        staff.is_staff = staff.is_active = True
        staff.is_superuser = False
        staff.set_password('demo1234')
        staff.save()
        staff.user_permissions.set(Permission.objects.filter(content_type__app_label='reports'))
        tutor_names = [('Emily', 'Chen'), ('James', 'Rivera'), ('Priya', 'Patel'), ('David', 'Williams'), ('Sofia', 'Martinez'), ('Michael', 'Osei')]
        tutors = []
        for index, (first, last) in enumerate(tutor_names, 1):
            tutor, _ = User.objects.get_or_create(username=f'tutor{index}')
            tutor.first_name, tutor.last_name = first, last
            tutor.email = f'tutor{index}@example.com'
            tutor.is_staff = tutor.is_superuser = False
            tutor.is_active = True
            tutor.set_password('demo1234')
            tutor.save()
            tutors.append(tutor)
        sites = [Site.objects.get_or_create(name=name)[0] for name in ['Bloomfield Public Library', 'East Orange Community Center', 'Montclair Public Library']]
        names = [('Ana', 'García'), ('Ahmed', 'Hassan'), ('Mei', 'Lin'), ('Fatima', 'Diallo'), ('Carlos', 'Mendoza'), ('Olena', 'Kovalenko'), ('Jean', 'Baptiste'), ('Amina', 'Yusuf'), ('Rosa', 'Santos'), ('Min', 'Park'), ('Luis', 'Alvarez'), ('Nadia', 'Rahman'), ('Grace', 'Okafor'), ('Dmytro', 'Shevchenko'), ('Linh', 'Nguyen')]
        students = [Student.objects.create(first_name=first, last_name=last, notes='Working on everyday reading and conversation.' if i % 4 == 0 else '') for i, (first, last) in enumerate(names)]
        schedules = ['1,3,5', '0,2', '1,3', '2,4', '0,4', '1,5']
        pairings = []
        for index in range(16):
            tutor_index = index // 3 if index < 12 else (4 if index < 14 else 5)
            assignment = Assignment(
                tutor=tutors[tutor_index], student=students[index % 15], site=sites[index % 3],
                meeting_days=schedules[index % len(schedules)], meeting_time=['6–7:30pm', '10–11:30am', '4–6pm'][index % 3],
                start_date=start + timedelta(days=7 if index == 10 else 0),
            )
            if index == 14 and today >= date(2026, 8, 18):
                assignment.status = 'STOPPED'
                assignment.stopped_on = date(2026, 8, 18)
                assignment.stopped_reason = 'Student moved out of the area and was referred to a nearby literacy program.'
            assignment.save()
            pairings.append(assignment)
            day = assignment.start_date
            previous_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
            end = min(today, assignment.stopped_on) if assignment.stopped_on else today
            while day <= end:
                if day.weekday() in assignment.meeting_weekdays():
                    # Two entirely missing reports, plus recent scheduled gaps for reminders.
                    missing_month = index in (12, 13) and day >= previous_month and day.month != 7
                    recent_gap = day >= today - timedelta(days=13) and (day.toordinal() + index) % 4 == 0
                    if not missing_month and not recent_gap and rng.random() > .035:
                        chance = rng.random()
                        status = 'HELD' if chance < .85 else ('SA' if chance < .94 else 'TA')
                        if day == date(2026, 7, 4):
                            status = 'H'
                        Session.objects.create(assignment=assignment, date=day, status=status,
                                               hours=rng.choice([Decimal('1'), Decimal('1.5'), Decimal('2')]) if status == 'HELD' else Decimal('0'),
                                               notes=rng.choice(['', '', 'Practiced reading and conversation.', 'Reviewed vocabulary and everyday forms.']) if status == 'HELD' else '',
                                               created_by=assignment.tutor)
                day += timedelta(days=1)
        catalog = list(Goal.objects.all())
        if not catalog:
            raise RuntimeError('Goal catalog is empty. Run migrate before seed_demo.')
        for index, days in enumerate([20, 33, 46, 58, 67, 74]):
            attained = start + timedelta(days=days)
            if attained <= today:
                StudentGoal.objects.create(student=students[index], goal=catalog[[0, 4, 7, 13, 1, 12][index]], attained_on=attained, recorded_by=pairings[index].tutor)
        if today >= date(2026, 8, 25):
            StudentGoal.objects.create(student=students[7], custom_text='Read a complete book in English independently', attained_on=date(2026, 8, 25), recorded_by=pairings[7].tutor)
        MonthLock.objects.get_or_create(year=2026, month=7, defaults={'locked_by': staff})
        self.stdout.write(self.style.SUCCESS(f'Demo ready: {Site.objects.count()} sites, {Student.objects.count()} students, {Assignment.objects.count()} assignments, {Session.objects.count()} sessions. July 2026 is locked.'))
        self.stdout.write('Log in as staff or tutor1–tutor6 with password demo1234.')
