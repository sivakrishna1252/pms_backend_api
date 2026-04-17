from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


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

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=Roles.choices, default=Roles.EMPLOYEE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

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
    name = models.CharField(max_length=255)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="milestones_created"
    )

    def __str__(self):
        return f"{self.project.name} - {self.name}"





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
    total_time_spent_seconds = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])

    def __str__(self):
        return self.title





#time logs table
class TimeLog(TimeStampedModel):
    class Source(models.TextChoices):
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
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="files", null=True, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="uploaded_files"
    )
    file = models.FileField(upload_to="task_files/")
    mime_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)
