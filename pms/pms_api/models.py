from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Max
from django.utils import timezone


def humanize_duration(seconds):
    total_seconds = max(int(seconds or 0), 0)
    if total_seconds < 60:
        return f"{total_seconds} sec"
    if total_seconds < 3600:
        minutes, rem_seconds = divmod(total_seconds, 60)
        return f"{minutes} min" + (f" {rem_seconds} sec" if rem_seconds else "")
    hours, rem = divmod(total_seconds, 3600)
    minutes = rem // 60
    return f"{hours} hr" + (f" {minutes} min" if minutes else "")


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

#user profiles
class UserProfile(TimeStampedModel):
    class Roles(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        BA = "BA", "Business Analyst"
        EMPLOYEE = "EMPLOYEE", "Employee"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"

    class ExperienceLevel(models.TextChoices):
        JUNIOR = "JUNIOR", "Junior"
        SENIOR = "SENIOR", "Senior"

    class Department(models.TextChoices):
        BACKEND = "BACKEND", "Backend"
        FRONTEND = "FRONTEND", "Frontend"
        FULLSTACK = "FULLSTACK", "Fullstack"

    class TechStack(models.TextChoices):
        PYTHON = "PYTHON", "Python"
        JAVA = "JAVA", "Java"
        NESTJS = "NESTJS", "NestJS"
        NEXTJS = "NEXTJS", "NextJS"
        REACT = "REACT", "React"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=Roles.choices, default=Roles.EMPLOYEE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    experience_level = models.CharField(max_length=20, choices=ExperienceLevel.choices, blank=True)
    department = models.CharField(max_length=20, choices=Department.choices, blank=True)
    tech_stack = models.CharField(max_length=20, choices=TechStack.choices, blank=True)
    tech_notes = models.TextField(blank=True, default="")

    def __str__(self):
        return f"{self.user.email} ({self.role})"




#projects table
class Project(TimeStampedModel):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "Planned"
        ACTIVE = "ACTIVE", "Active"
        COMPLETED = "COMPLETED", "Completed"
        DELAYED = "DELAYED", "Delayed"
        ARCHIVED = "ARCHIVED", "Archived"

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="projects_created"
    )
    start_date = models.DateField()
    deadline = models.DateField()
    document = models.FileField(upload_to="project_docs/", null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)

    def __str__(self):
        return self.name




#milestones table
class Milestone(TimeStampedModel):
    class Status(models.TextChoices):
        NOT_STARTED = "NOT_STARTED", "Not Started"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"
        DELAYED = "DELAYED", "Delayed"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="milestones")
    milestone_no = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    start_date = models.DateField()
    end_date = models.DateField()
    document = models.FileField(upload_to="milestone_docs/", null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="milestones_created"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "milestone_no"], name="uniq_milestone_no_per_project"),
        ]

    def save(self, *args, **kwargs):
        if not self.milestone_no:
            last_number = (
                Milestone.objects.filter(project=self.project).aggregate(max_no=Max("milestone_no")).get("max_no") or 0
            )
            self.milestone_no = last_number + 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.project.name} - M{self.milestone_no}: {self.name}"





#tasks table
class Task(TimeStampedModel):
    class Status(models.TextChoices):
        NOT_STARTED = "NOT_STARTED", "Not Started"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        PAUSED = "PAUSED", "Paused"
        COMPLETED = "COMPLETED", "Completed"
        DELAYED = "DELAYED", "Delayed"
        BLOCKED = "BLOCKED", "Blocked"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    milestone = models.ForeignKey(Milestone, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks_assigned",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasks_created"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    priority = models.CharField(max_length=20, blank=True)
    deadline = models.DateField(null=True, blank=True)
    document = models.FileField(upload_to="task_docs/", null=True, blank=True)
    total_time_spent_seconds = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])

    def __str__(self):
        return self.title

    @property
    def total_time_spent_display(self):
        return humanize_duration(self.total_time_spent_seconds)





#time logs table
class TimeLog(TimeStampedModel):
    class Source(models.TextChoices):
        MANUAL_PAUSE = "MANUAL_PAUSE", "Manual Pause"
        MANUAL_STOP = "MANUAL_STOP", "Manual Stop"
        AUTO_STOP_8PM = "AUTO_STOP_8PM", "Auto Stop 8PM"

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="time_logs")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="time_logs")
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL_STOP)

    @property
    def is_active(self):
        return self.end_time is None

    @property
    def duration_display(self):
        return humanize_duration(self.duration_seconds)

    def stop(self, source=Source.MANUAL_STOP):
        if self.end_time:
            return
        self.end_time = timezone.now()
        self.duration_seconds = int((self.end_time - self.start_time).total_seconds())
        self.source = source
        self.save(update_fields=["end_time", "duration_seconds", "source"])
        self.task.total_time_spent_seconds += self.duration_seconds
        self.task.save(update_fields=["total_time_spent_seconds"])




#notifications table
class Notification(TimeStampedModel):
    class RefType(models.TextChoices):
        TASK = "TASK", "Task"
        MILESTONE = "MILESTONE", "Milestone"
        PROJECT = "PROJECT", "Project"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    type = models.CharField(max_length=100)
    title = models.CharField(max_length=255)
    message = models.TextField()
    ref_type = models.CharField(max_length=20, choices=RefType.choices, blank=True)
    ref_id = models.PositiveIntegerField(null=True, blank=True)
    is_read = models.BooleanField(default=False)



#file attachments table
class FileAttachment(TimeStampedModel):
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, related_name="files", null=True, blank=True)
    milestone = models.ForeignKey(Milestone, on_delete=models.SET_NULL, related_name="files", null=True, blank=True)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="files", null=True, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="uploaded_files"
    )
    file = models.FileField(upload_to="task_files/")
    mime_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)
