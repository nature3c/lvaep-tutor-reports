from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .utils import is_locked


class Site(models.Model):
    name = models.CharField(max_length=200, unique=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Student(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['last_name', 'first_name']

    def __str__(self):
        return f'{self.first_name} {self.last_name}'


class Assignment(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        STOPPED = 'STOPPED', 'Stopped'

    tutor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assignments')
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='assignments')
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='assignments')
    meeting_days = models.CharField(max_length=13, validators=[RegexValidator(r'^[0-6](,[0-6])*$', 'Use comma-separated weekdays, 0 (Monday) through 6 (Sunday).')])
    meeting_time = models.CharField(max_length=100)
    start_date = models.DateField()
    status = models.CharField(max_length=7, choices=Status.choices, default=Status.ACTIVE)
    stopped_on = models.DateField(null=True, blank=True)
    stopped_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['student__last_name', 'student__first_name', 'pk']
        constraints = [models.UniqueConstraint(fields=['tutor', 'student'], condition=Q(status='ACTIVE'), name='unique_active_pairing')]

    def __str__(self):
        return f'{self.student} / {self.tutor.get_full_name() or self.tutor.username}'

    def meeting_weekdays(self):
        return sorted(set(int(d) for d in self.meeting_days.split(',') if d.strip()))

    @property
    def meeting_days_display(self):
        names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        return ', '.join(names[d] for d in self.meeting_weekdays())

    def clean(self):
        super().clean()
        if self.status == self.Status.STOPPED:
            if not self.stopped_on:
                raise ValidationError({'stopped_on': 'Enter the date tutoring stopped.'})
            if not self.stopped_reason.strip():
                raise ValidationError({'stopped_reason': 'Please give a reason.'})
            if self.start_date and self.stopped_on < self.start_date:
                raise ValidationError({'stopped_on': 'The stop date cannot precede the start date.'})
            if self.stopped_on > timezone.localdate():
                raise ValidationError({'stopped_on': 'The stop date cannot be in the future.'})
        elif self.stopped_on or self.stopped_reason:
            raise ValidationError('Active assignments cannot have stop details.')
        if self.pk:
            if self.start_date and self.sessions.filter(date__lt=self.start_date).exists():
                raise ValidationError({'start_date': 'Existing sessions precede this start date.'})
            if self.stopped_on and self.sessions.filter(date__gt=self.stopped_on).exists():
                raise ValidationError({'stopped_on': 'Existing sessions fall after this date.'})
            old = type(self).objects.get(pk=self.pk)
            if any(getattr(old, field) != getattr(self, field) for field in ('site_id', 'tutor_id', 'student_id')):
                if any(is_locked(day) for day in self.sessions.values_list('date', flat=True)):
                    raise ValidationError('A pairing with locked sessions cannot change tutor, student, or site.')

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class SessionQuerySet(models.QuerySet):
    def delete(self):
        for session in self:
            session.assert_unlocked()
        return super().delete()


class Session(models.Model):
    class Status(models.TextChoices):
        HELD = 'HELD', 'Held'
        TA = 'TA', 'Tutor absent'
        SA = 'SA', 'Student absent'
        H = 'H', 'Holiday'

    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name='sessions')
    date = models.DateField(default=timezone.localdate)
    hours = models.DecimalField(max_digits=4, decimal_places=2)
    status = models.CharField(max_length=4, choices=Status.choices, default=Status.HELD)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='recorded_sessions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = SessionQuerySet.as_manager()

    class Meta:
        ordering = ['-date', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['assignment', 'date'], name='one_session_per_pairing_day'),
            models.CheckConstraint(condition=(Q(status='HELD', hours__in=[Decimal(n) / 4 for n in range(1, 33)]) | Q(status__in=['TA', 'SA', 'H'], hours=0)), name='valid_session_status_hours'),
        ]

    def __str__(self):
        return f'{self.assignment} · {self.date} · {self.get_status_display()}'

    def assert_unlocked(self):
        if self.date and is_locked(self.date):
            raise ValidationError('This month is locked. Ask staff to unlock it before making changes.')
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).first()
            if old and is_locked(old.date):
                raise ValidationError('The original month is locked. This session cannot be changed or moved.')

    def clean(self):
        super().clean()
        errors = {}
        if self.hours is not None:
            if self.status == self.Status.HELD:
                if not Decimal('0.25') <= self.hours <= Decimal('8') or self.hours % Decimal('0.25'):
                    errors['hours'] = 'Held sessions must be 0.25–8 hours in quarter-hour steps.'
            elif self.status in (self.Status.TA, self.Status.SA, self.Status.H) and self.hours != 0:
                errors['hours'] = 'Absences and holidays must have zero hours.'
        if self.date:
            if self.date > timezone.localdate():
                errors['date'] = 'Sessions cannot be recorded in the future.'
            if self.assignment_id:
                if self.date < self.assignment.start_date:
                    errors['date'] = 'This date is before the pairing began.'
                elif self.assignment.status == Assignment.Status.STOPPED and self.assignment.stopped_on and self.date > self.assignment.stopped_on:
                    errors['date'] = 'This date is after tutoring stopped.'
        if errors:
            raise ValidationError(errors)
        self.assert_unlocked()

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.assert_unlocked()
        return super().delete(*args, **kwargs)


class Goal(models.Model):
    class Category(models.TextChoices):
        A = 'A', 'A. Economic'
        B = 'B', 'B. Educational'
        C = 'C', 'C. Family'
        D = 'D', 'D. Societal/Community'
        E = 'E', 'E. Other'

    category = models.CharField(max_length=1, choices=Category.choices)
    text = models.CharField(max_length=300)
    is_reportable = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['category', 'order', 'pk']

    def __str__(self):
        return f'{self.category}. {self.text}'


class StudentGoal(models.Model):
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name='achievements')
    goal = models.ForeignKey(Goal, null=True, blank=True, on_delete=models.PROTECT, related_name='attainments')
    custom_text = models.CharField(max_length=300, blank=True)
    attained_on = models.DateField(default=timezone.localdate)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='recorded_goals')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-attained_on', 'pk']
        constraints = [models.UniqueConstraint(fields=['student', 'goal'], condition=Q(goal__isnull=False), name='unique_student_catalog_goal'),
                       models.CheckConstraint(condition=(Q(goal__isnull=False, custom_text='') | (Q(goal__isnull=True) & ~Q(custom_text=''))), name='catalog_or_custom_goal')]

    def __str__(self):
        return self.goal.text if self.goal_id else self.custom_text

    def clean(self):
        super().clean()
        if not self.goal_id and not self.custom_text.strip():
            raise ValidationError({'custom_text': 'Enter a description for an Other achievement.'})
        if self.goal_id and self.custom_text:
            raise ValidationError({'custom_text': 'Use either a catalog goal or custom text.'})
        if self.attained_on and self.attained_on > timezone.localdate():
            raise ValidationError({'attained_on': 'An achievement cannot be in the future.'})
        self.assert_unlocked()

    def assert_unlocked(self):
        if self.attained_on and is_locked(self.attained_on):
            raise ValidationError('This achievement belongs to a locked month.')
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            if is_locked(original.attained_on):
                raise ValidationError('This achievement belongs to a locked month.')

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.assert_unlocked()
        return super().delete(*args, **kwargs)


class MonthLock(models.Model):
    year = models.PositiveSmallIntegerField(validators=[MinValueValidator(1900), MaxValueValidator(9999)])
    month = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    locked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-year', '-month']
        constraints = [models.UniqueConstraint(fields=['year', 'month'], name='unique_month_lock'),
                       models.CheckConstraint(condition=Q(month__gte=1, month__lte=12), name='valid_lock_month')]

    def __str__(self):
        return f'{self.year}-{self.month:02d}'
