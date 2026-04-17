from django.contrib import admin
from .models import FileAttachment, Milestone, Notification, Project, Task, TimeLog, UserProfile

admin.site.register(UserProfile)


admin.site.register(Project)
admin.site.register(Milestone)
admin.site.register(Task)
admin.site.register(TimeLog)
admin.site.register(Notification)
admin.site.register(FileAttachment)
