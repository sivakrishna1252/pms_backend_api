from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import IntegrityError
from decimal import Decimal
from django.core.files.uploadedfile import UploadedFile
from pathlib import Path
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from .models import (
    FileAttachment,
    Milestone,
    Notification,
    Project,
    Task,
    TimeLog,
    UserProfile,
    humanize_duration,
)
User = get_user_model()

ALLOWED_DOCUMENT_EXTENSIONS = {".doc", ".docx", ".md"}


def validate_uploaded_document(value):
    if value in (None, ""):
        return value
    if not isinstance(value, UploadedFile):
        raise serializers.ValidationError("Upload a valid file using multipart/form-data.")
    extension = Path(value.name or "").suffix.lower()
    if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise serializers.ValidationError("Only .doc, .docx, and .md files are allowed.")
    return value


def normalize_choice_input(value, choices_enum, aliases=None):
    if value in (None, ""):
        return value
    if not isinstance(value, str):
        return value

    aliases = aliases or {}
    normalized = value.strip()
    upper = normalized.upper()

    # Accept direct enum value like "EMPLOYEE"
    if upper in choices_enum.values:
        return upper

    # Accept display label like "Employee"
    for enum_value, enum_label in choices_enum.choices:
        if normalized.lower() == str(enum_label).strip().lower():
            return enum_value

    # Accept custom shortcuts like "jr"
    if upper in aliases:
        return aliases[upper]

    return value


class FlexibleChoiceField(serializers.ChoiceField):
    def __init__(self, *args, normalizer=None, **kwargs):
        self.normalizer = normalizer
        super().__init__(*args, **kwargs)

    def to_internal_value(self, data):
        if self.normalizer:
            data = self.normalizer(data)
        return super().to_internal_value(data)


#login serializer
class AuthLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        allowed_domain = getattr(settings, "ALLOWED_OFFICE_EMAIL_DOMAIN", "@apparatus.solutions")
        if not attrs["email"].lower().endswith(allowed_domain):
            raise serializers.ValidationError(f"Only office emails ending with {allowed_domain} are allowed.")
        email_norm = attrs["email"].strip().lower()
        password = attrs["password"]

        # Django User.username is the auth identifier; this app also accepts login by email field
        # when username differs (e.g. username "siva", email "siva@apparatus.solutions").
        user = authenticate(username=email_norm, password=password)
        if not user:
            candidate = User.objects.filter(email__iexact=email_norm).first()
            if candidate:
                user = authenticate(username=candidate.get_username(), password=password)
        if not user:
            raise serializers.ValidationError("Invalid credentials.")
        attrs["user"] = user
        return attrs



#user serializer
class UserSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    experience_level = serializers.SerializerMethodField()
    department = serializers.SerializerMethodField()
    tech_stack = serializers.SerializerMethodField()
    tech_notes = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "first_name",
            "last_name",
            "email",
            "role",
            "status",
            "experience_level",
            "department",
            "tech_stack",
            "tech_notes",
            "date_joined",
            "last_login",
        ]
        read_only_fields = ["id", "date_joined", "last_login"]

    def get_role(self, obj) -> str:
        profile = getattr(obj, "profile", None)
        return getattr(profile, "role", None)

    def get_status(self, obj) -> str:
        profile = getattr(obj, "profile", None)
        return getattr(profile, "status", None)

    def get_experience_level(self, obj) -> str | None:
        profile = getattr(obj, "profile", None)
        return getattr(profile, "experience_level", None)

    def get_department(self, obj) -> str:
        profile = getattr(obj, "profile", None)
        return getattr(profile, "department", None)

    def get_tech_stack(self, obj) -> str:
        profile = getattr(obj, "profile", None)
        return getattr(profile, "tech_stack", None)

    def get_tech_notes(self, obj) -> str:
        profile = getattr(obj, "profile", None)
        return (getattr(profile, "tech_notes", None) or "") or ""


#user create serializer
class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6)
    role = FlexibleChoiceField(
        choices=UserProfile.Roles.choices,
        normalizer=lambda v: normalize_choice_input(v, UserProfile.Roles),
    )
    status = FlexibleChoiceField(
        choices=UserProfile.Status.choices,
        default=UserProfile.Status.ACTIVE,
        normalizer=lambda v: normalize_choice_input(v, UserProfile.Status),
    )
    experience_level = FlexibleChoiceField(
        choices=UserProfile.ExperienceLevel.choices, required=False, allow_blank=True, default=""
        , normalizer=lambda v: normalize_choice_input(
            v,
            UserProfile.ExperienceLevel,
            aliases={"JR": UserProfile.ExperienceLevel.JUNIOR, "SR": UserProfile.ExperienceLevel.SENIOR},
        )
    )
    department = FlexibleChoiceField(
        choices=UserProfile.Department.choices, required=False, allow_blank=True, default=""
        , normalizer=lambda v: normalize_choice_input(v, UserProfile.Department)
    )
    tech_stack = FlexibleChoiceField(
        choices=UserProfile.TechStack.choices, required=False, allow_blank=True, default=""
        , normalizer=lambda v: normalize_choice_input(v, UserProfile.TechStack)
    )
    tech_notes = serializers.CharField(required=False, allow_blank=True, default="", max_length=4000)

    class Meta:
        model = User
        fields = [
            "id",
            "first_name",
            "last_name",
            "email",
            "password",
            "role",
            "status",
            "experience_level",
            "department",
            "tech_stack",
            "tech_notes",
        ]
        read_only_fields = ["id"]

    def validate_email(self, value):
        allowed_domain = getattr(settings, "ALLOWED_OFFICE_EMAIL_DOMAIN", "@apparatus.solutions")
        if not value.lower().endswith(allowed_domain):
            raise serializers.ValidationError(f"Only office email domain ({allowed_domain}) is allowed.")
        existing_user = User.objects.filter(username__iexact=value).first()
        if existing_user:
            existing_role = getattr(getattr(existing_user, "profile", None), "role", "UNKNOWN")
            raise serializers.ValidationError(
                f"This email is already registered with role {existing_role}. "
                "You cannot create another account with the same email."
            )
        return value

    def validate(self, attrs):
        request = self.context.get("request")
        requester_role = getattr(getattr(getattr(request, "user", None), "profile", None), "role", None)
        if requester_role != UserProfile.Roles.ADMIN:
            raise serializers.ValidationError("Only admin users can create users.")

        role = attrs.get("role")
        experience_level = attrs.get("experience_level", "")
        department = attrs.get("department", "")
        tech_stack = (attrs.get("tech_stack") or "").strip()
        tech_notes_stripped = (attrs.get("tech_notes") or "").strip()

        if role == UserProfile.Roles.EMPLOYEE:
            required_personal = {
                "experience_level": experience_level,
                "department": department,
            }
            missing_personal = [field for field, value in required_personal.items() if not value]
            if missing_personal:
                raise serializers.ValidationError(
                    {
                        field: "This field is required when role is EMPLOYEE."
                        for field in missing_personal
                    }
                )
            if not tech_stack and not tech_notes_stripped:
                raise serializers.ValidationError(
                    {"tech_stack": "Select at least one tech stack or add notes for employees."}
                )
            attrs["tech_notes"] = attrs.get("tech_notes") or ""
            if tech_stack == "":
                attrs["tech_stack"] = UserProfile.TechStack.PYTHON
        else:
            attrs["experience_level"] = ""
            attrs["department"] = ""
            attrs["tech_stack"] = ""
            attrs["tech_notes"] = ""

        return attrs

    def create(self, validated_data):
        role = validated_data.pop("role")
        profile_status = validated_data.pop("status", UserProfile.Status.ACTIVE)
        experience_level = validated_data.pop("experience_level", "")
        department = validated_data.pop("department", "")
        tech_stack = validated_data.pop("tech_stack", "")
        tech_notes = validated_data.pop("tech_notes", "")
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.username = validated_data["email"]
        user.set_password(password)
        try:
            user.save()
        except IntegrityError:
            raise serializers.ValidationError(
                {"email": "This email is already registered. Use a different email for new users."}
            )
        UserProfile.objects.create(
            user=user,
            role=role,
            status=profile_status,
            experience_level=experience_level,
            department=department,
            tech_stack=tech_stack,
            tech_notes=tech_notes or "",
        )
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6, required=False, allow_blank=False)
    role = FlexibleChoiceField(
        choices=UserProfile.Roles.choices,
        required=False,
        normalizer=lambda v: normalize_choice_input(v, UserProfile.Roles),
    )
    status = FlexibleChoiceField(
        choices=UserProfile.Status.choices,
        required=False,
        normalizer=lambda v: normalize_choice_input(v, UserProfile.Status),
    )
    experience_level = FlexibleChoiceField(
        choices=UserProfile.ExperienceLevel.choices,
        required=False,
        allow_blank=True,
        normalizer=lambda v: normalize_choice_input(
            v,
            UserProfile.ExperienceLevel,
            aliases={"JR": UserProfile.ExperienceLevel.JUNIOR, "SR": UserProfile.ExperienceLevel.SENIOR},
        ),
    )
    department = FlexibleChoiceField(
        choices=UserProfile.Department.choices,
        required=False,
        allow_blank=True,
        normalizer=lambda v: normalize_choice_input(v, UserProfile.Department),
    )
    tech_stack = FlexibleChoiceField(
        choices=UserProfile.TechStack.choices,
        required=False,
        allow_blank=True,
        normalizer=lambda v: normalize_choice_input(v, UserProfile.TechStack),
    )
    tech_notes = serializers.CharField(required=False, allow_blank=True, max_length=4000)

    class Meta:
        model = User
        fields = [
            "id",
            "first_name",
            "last_name",
            "email",
            "password",
            "role",
            "status",
            "experience_level",
            "department",
            "tech_stack",
            "tech_notes",
        ]
        read_only_fields = ["id"]

    def validate_email(self, value):
        allowed_domain = getattr(settings, "ALLOWED_OFFICE_EMAIL_DOMAIN", "@apparatus.solutions")
        if not value.lower().endswith(allowed_domain):
            raise serializers.ValidationError(f"Only office email domain ({allowed_domain}) is allowed.")
        existing_user = User.objects.filter(username__iexact=value).exclude(id=self.instance.id).first()
        if existing_user:
            raise serializers.ValidationError("This email is already registered. Use a different email.")
        return value

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        role = validated_data.pop("role", None)
        profile_status = validated_data.pop("status", None)
        experience_level = validated_data.pop("experience_level", None)
        department = validated_data.pop("department", None)
        tech_stack = validated_data.pop("tech_stack", None)
        tech_notes = validated_data.pop("tech_notes", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if "email" in validated_data:
            instance.username = validated_data["email"]
        instance.save()

        profile, _ = UserProfile.objects.get_or_create(user=instance)
        profile_changed_fields = []
        if role is not None:
            profile.role = role
            profile_changed_fields.append("role")
        if profile_status is not None:
            profile.status = profile_status
            profile_changed_fields.append("status")
        if experience_level is not None:
            profile.experience_level = experience_level
            profile_changed_fields.append("experience_level")
        if department is not None:
            profile.department = department
            profile_changed_fields.append("department")
        if tech_stack is not None:
            profile.tech_stack = tech_stack
            profile_changed_fields.append("tech_stack")
        if tech_notes is not None:
            profile.tech_notes = tech_notes
            profile_changed_fields.append("tech_notes")

        target_role = role if role is not None else profile.role
        if target_role != UserProfile.Roles.EMPLOYEE:
            if profile.experience_level:
                profile.experience_level = ""
                profile_changed_fields.append("experience_level")
            if profile.department:
                profile.department = ""
                profile_changed_fields.append("department")
            if profile.tech_stack:
                profile.tech_stack = ""
                profile_changed_fields.append("tech_stack")
            if profile.tech_notes:
                profile.tech_notes = ""
                profile_changed_fields.append("tech_notes")
        else:
            missing_employee_fields = []
            if not profile.experience_level:
                missing_employee_fields.append("experience_level")
            if not profile.department:
                missing_employee_fields.append("department")
            tn = (profile.tech_notes or "").strip()
            ts = (profile.tech_stack or "").strip()
            if not ts and not tn:
                missing_employee_fields.append("tech_stack")
            if missing_employee_fields:
                raise serializers.ValidationError(
                    {field: "This field is required when role is EMPLOYEE." for field in missing_employee_fields}
                )
        if profile_changed_fields:
            profile.save(update_fields=list(dict.fromkeys(profile_changed_fields)))

        if password:
            instance.set_password(password)
            instance.save(update_fields=["password"])

        return instance



#project serializer
class ProjectSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField(read_only=True)
    progress_percent = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "description",
            "created_by",
            "created_by_name",
            "start_date",
            "deadline",
            "document",
            "status",
            "progress_percent",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]

    def get_created_by_name(self, obj) -> str:
        if not obj.created_by:
            return ""
        return obj.created_by.get_full_name().strip() or obj.created_by.email

    def get_progress_percent(self, obj):
        total = getattr(obj, "_total_estimated_hours", None)
        weighted = getattr(obj, "_weighted_complete_hours", None)
        if total is None or weighted is None:
            from .progress import project_progress_data

            return project_progress_data(obj).get("progress_percent")
        total = total or Decimal("0")
        weighted = weighted or Decimal("0")
        if total <= 0:
            return None
        return float((weighted / total * Decimal("100")).quantize(Decimal("0.01")))

    def validate(self, attrs):
        request = self.context.get("request")
        role = getattr(getattr(getattr(request, "user", None), "profile", None), "role", None)
        if "document" in attrs:
            attrs["document"] = validate_uploaded_document(attrs["document"])
        if self.instance and "status" in attrs and attrs["status"] != self.instance.status:
            if role != UserProfile.Roles.ADMIN:
                raise serializers.ValidationError({"status": "Only Admin can modify project status."})
        if self.instance and "deadline" in attrs and attrs["deadline"] != self.instance.deadline:
            if role != UserProfile.Roles.ADMIN:
                raise serializers.ValidationError({"deadline": "Only Admin can modify project deadline."})
        return attrs





#milestone serializer
class MilestoneSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField(read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    progress_percent = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Milestone
        fields = [
            "id",
            "milestone_no",
            "project",
            "project_name",
            "name",
            "description",
            "start_date",
            "end_date",
            "document",
            "status",
            "progress_percent",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "milestone_no", "created_by", "created_at", "updated_at"]

    def get_created_by_name(self, obj) -> str:
        if not obj.created_by:
            return ""
        return obj.created_by.get_full_name().strip() or obj.created_by.email

    def get_progress_percent(self, obj):
        total = getattr(obj, "_total_estimated_hours", None)
        weighted = getattr(obj, "_weighted_complete_hours", None)
        if total is None or weighted is None:
            from .progress import milestone_progress_data

            return milestone_progress_data(obj).get("progress_percent")
        total = total or Decimal("0")
        weighted = weighted or Decimal("0")
        if total <= 0:
            return None
        return float((weighted / total * Decimal("100")).quantize(Decimal("0.01")))

    def validate(self, attrs):
        if "document" in attrs:
            attrs["document"] = validate_uploaded_document(attrs["document"])
        project = attrs.get("project", self.instance.project if self.instance else None)
        end_date = attrs.get("end_date", self.instance.end_date if self.instance else None)
        if project and end_date and end_date > project.deadline:
            raise serializers.ValidationError(
                {"end_date": "Expected date cannot be after the project deadline."}
            )
        if self.instance and "end_date" in attrs and attrs["end_date"] != self.instance.end_date:
            request = self.context.get("request")
            role = getattr(getattr(getattr(request, "user", None), "profile", None), "role", None)
            if role not in {UserProfile.Roles.ADMIN, UserProfile.Roles.BA}:
                raise serializers.ValidationError(
                    {"end_date": "Only Admin or BA can modify milestone expected date."}
                )
        return attrs




#task serializer
class TaskSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField(read_only=True)
    assigned_to_name = serializers.SerializerMethodField(read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    milestone_name = serializers.CharField(source="milestone.name", read_only=True)
    project_document = serializers.FileField(source="project.document", read_only=True)
    milestone_document = serializers.FileField(source="milestone.document", read_only=True)
    total_time_spent_display = serializers.SerializerMethodField(read_only=True)
    timer_state = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Task
        fields = [
            "id",
            "project",
            "project_name",
            "project_document",
            "milestone",
            "milestone_name",
            "milestone_document",
            "title",
            "description",
            "assigned_to",
            "assigned_to_name",
            "created_by",
            "created_by_name",
            "status",
            "priority",
            "deadline",
            "document",
            "estimated_hours",
            "total_time_spent_seconds",
            "total_time_spent_display",
            "timer_state",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "total_time_spent_seconds", "created_at", "updated_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        role = getattr(getattr(getattr(request, "user", None), "profile", None), "role", None)
        if role == UserProfile.Roles.EMPLOYEE:
            data.pop("total_time_spent_seconds", None)
            data.pop("total_time_spent_display", None)
        return data

    def validate_document(self, value):
        return validate_uploaded_document(value)

    def get_created_by_name(self, obj) -> str:
        if not obj.created_by:
            return ""
        return obj.created_by.get_full_name().strip() or obj.created_by.email

    def get_assigned_to_name(self, obj) -> str:
        if not obj.assigned_to:
            return ""
        return obj.assigned_to.get_full_name().strip() or obj.assigned_to.email

    def get_total_time_spent_display(self, obj) -> str:
        return humanize_duration(getattr(obj, "total_time_spent_seconds", None))

    def get_timer_state(self, obj) -> str | None:
        """STARTED when assignee has an open TimeLog; otherwise None (idle / not running)."""
        assignee_id = obj.assigned_to_id
        if not assignee_id:
            return None
        if TimeLog.objects.filter(task=obj, user_id=assignee_id, end_time__isnull=True).exists():
            return "STARTED"
        return None

    def validate(self, attrs):
        from datetime import date, datetime

        instance = self.instance

        def _pk(val):
            if val is None:
                return None
            return getattr(val, "pk", val)

        def _date_only(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val.date()
            if isinstance(val, date):
                return val
            return val

        project_id = attrs.get("project", None)
        project_id = _pk(project_id)
        if project_id is None and instance is not None:
            project_id = instance.project_id

        milestone_val = attrs.get("milestone", serializers.empty)
        if milestone_val is serializers.empty:
            milestone_id = instance.milestone_id if instance else None
        else:
            milestone_id = _pk(milestone_val)

        deadline_val = attrs.get("deadline", serializers.empty)
        if deadline_val is serializers.empty:
            deadline = instance.deadline if instance else None
        else:
            deadline = deadline_val
        deadline = _date_only(deadline)

        if deadline and project_id is not None:
            proj_deadline = (
                Project.objects.filter(pk=project_id).values_list("deadline", flat=True).first()
            )
            proj_deadline = _date_only(proj_deadline)
            if proj_deadline is not None and deadline > proj_deadline:
                raise serializers.ValidationError(
                    {"deadline": "Expected date cannot be after the project deadline."}
                )
        if deadline and milestone_id is not None:
            ms_end = Milestone.objects.filter(pk=milestone_id).values_list("end_date", flat=True).first()
            ms_end = _date_only(ms_end)
            if ms_end is not None and deadline > ms_end:
                raise serializers.ValidationError(
                    {"deadline": "Expected date cannot be after the milestone end date."}
                )
        return attrs




#time log serializer
class TimeLogSerializer(serializers.ModelSerializer):
    duration_display = serializers.CharField(read_only=True)

    class Meta:
        model = TimeLog
        fields = "__all__"
        read_only_fields = ["id", "duration_seconds", "created_at", "updated_at"]



    
#notification serializer
class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]




#file attachment serializer
class FileAttachmentSerializer(serializers.ModelSerializer):
    ALLOWED_FILE_EXTENSIONS = {".doc", ".docx", ".md"}

    project_name = serializers.SerializerMethodField(read_only=True)
    milestone_name = serializers.SerializerMethodField(read_only=True)
    task_name = serializers.SerializerMethodField(read_only=True)
    resolved_project_id = serializers.SerializerMethodField(read_only=True)
    resolved_project_name = serializers.SerializerMethodField(read_only=True)
    linked_to_type = serializers.SerializerMethodField(read_only=True)
    linked_to_id = serializers.SerializerMethodField(read_only=True)
    linked_to_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = FileAttachment
        fields = [
            "id",
            "project",
            "project_name",
            "milestone",
            "milestone_name",
            "task",
            "task_name",
            "resolved_project_id",
            "resolved_project_name",
            "linked_to_type",
            "linked_to_id",
            "linked_to_name",
            "file",
            "uploaded_by",
            "mime_type",
            "size_bytes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "uploaded_by", "mime_type", "size_bytes", "created_at", "updated_at"]

    def get_project_name(self, obj) -> str:
        if obj.project_id:
            return obj.project.name
        return ""

    def get_milestone_name(self, obj) -> str:
        if obj.milestone_id:
            return obj.milestone.name
        return ""

    def get_task_name(self, obj) -> str:
        if obj.task_id:
            return obj.task.title
        return ""

    def get_resolved_project_id(self, obj) -> int | None:
        if obj.project_id:
            return obj.project_id
        if obj.milestone_id:
            return obj.milestone.project_id
        if obj.task_id:
            return obj.task.project_id
        return None

    def get_resolved_project_name(self, obj) -> str:
        if obj.project_id:
            return obj.project.name
        if obj.milestone_id:
            return obj.milestone.project.name
        if obj.task_id:
            return obj.task.project.name
        return ""

    def get_linked_to_type(self, obj) -> str:
        if obj.project_id:
            return "project"
        if obj.milestone_id:
            return "milestone"
        if obj.task_id:
            return "task"
        return ""

    def get_linked_to_id(self, obj) -> int | None:
        if obj.project_id:
            return obj.project_id
        if obj.milestone_id:
            return obj.milestone_id
        if obj.task_id:
            return obj.task_id
        return None

    def get_linked_to_name(self, obj) -> str:
        if obj.project_id:
            return obj.project.name
        if obj.milestone_id:
            return obj.milestone.name
        if obj.task_id:
            return obj.task.title
        return ""

    def validate_file(self, value):
        # Reject plain strings/URLs and enforce multipart binary uploads.
        if not isinstance(value, UploadedFile):
            raise serializers.ValidationError("Upload a valid file using multipart/form-data.")
        extension = Path(value.name or "").suffix.lower()
        if extension not in self.ALLOWED_FILE_EXTENSIONS:
            raise serializers.ValidationError("Only .doc, .docx, and .md files are allowed.")
        return value

    def validate(self, attrs):
        project = attrs.get("project", getattr(self.instance, "project", None))
        milestone = attrs.get("milestone", getattr(self.instance, "milestone", None))
        task = attrs.get("task", getattr(self.instance, "task", None))

        linked_count = sum(1 for item in [project, milestone, task] if item is not None)
        if linked_count != 1:
            raise serializers.ValidationError(
                "Upload must be linked to exactly one purpose: project or milestone or task."
            )
        return attrs


class AssignTaskSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()


class TaskStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Task.Status.choices)


class EmptySerializer(serializers.Serializer):
    pass


class FileUploadRequestSerializer(serializers.Serializer):
    project = serializers.IntegerField(required=False, allow_null=True)
    milestone = serializers.IntegerField(required=False, allow_null=True)
    task = serializers.IntegerField(required=False, allow_null=True)
    file = serializers.FileField()

    def validate(self, attrs):
        linked_count = sum(1 for key in ["project", "milestone", "task"] if attrs.get(key) is not None)
        if linked_count != 1:
            raise serializers.ValidationError(
                "Provide exactly one link field: project or milestone or task."
            )
        return attrs


#auth response serializer
class AuthResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserSerializer()

    @staticmethod
    def build(user):
        refresh = RefreshToken.for_user(user)
        return {"access": str(refresh.access_token), "refresh": str(refresh), "user": UserSerializer(user).data}


class RefreshTokenRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class MeUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    email = serializers.EmailField(required=False)
    current_password = serializers.CharField(required=False, allow_blank=True, write_only=True)
    new_password = serializers.CharField(required=False, allow_blank=True, min_length=6, write_only=True)

    def validate_email(self, value):
        allowed_domain = getattr(settings, "ALLOWED_OFFICE_EMAIL_DOMAIN", "@apparatus.solutions")
        if not value.lower().endswith(allowed_domain):
            raise serializers.ValidationError(f"Only office email domain ({allowed_domain}) is allowed.")
        user = self.context["request"].user
        existing = User.objects.filter(username__iexact=value).exclude(id=user.id).first()
        if existing:
            raise serializers.ValidationError("This email is already registered. Use a different email.")
        return value

    def validate(self, attrs):
        new_password = attrs.get("new_password", "")
        current_password = attrs.get("current_password", "")
        user = self.context["request"].user
        if new_password:
            if not current_password:
                raise serializers.ValidationError({"current_password": "Current password is required."})
            if not user.check_password(current_password):
                raise serializers.ValidationError({"current_password": "Current password is incorrect."})
        return attrs

    def update(self, instance, validated_data):
        email = validated_data.get("email")
        if "first_name" in validated_data:
            instance.first_name = validated_data["first_name"]
        if "last_name" in validated_data:
            instance.last_name = validated_data["last_name"]
        if email is not None:
            instance.email = email
            instance.username = email
        update_fields = ["first_name", "last_name"]
        if email is not None:
            update_fields.extend(["email", "username"])
        instance.save(update_fields=list(dict.fromkeys(update_fields)))

        new_password = validated_data.get("new_password")
        if new_password:
            instance.set_password(new_password)
            instance.save(update_fields=["password"])
        return instance


class AdminPasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()
    new_password = serializers.CharField(min_length=6, write_only=True)


class AdminForgotPasswordRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class AdminForgotPasswordVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(min_length=6, max_length=6)
    new_password = serializers.CharField(min_length=6, write_only=True)


class AdminAIAskSerializer(serializers.Serializer):
    """
    Send `question` only for most calls. Optional filters are for advanced use; 0 or omitted means no filter.
    """

    question = serializers.CharField(max_length=4000, min_length=1, trim_whitespace=True)
    project_id = serializers.IntegerField(required=False, allow_null=True)
    milestone_id = serializers.IntegerField(required=False, allow_null=True)
    task_id = serializers.IntegerField(required=False, allow_null=True)

    def validate(self, attrs):
        for key in ("project_id", "milestone_id", "task_id"):
            v = attrs.get(key)
            if v is None or v == 0:
                attrs[key] = None
        return attrs


class DeadlineChangeRequestSerializer(serializers.Serializer):
    new_deadline = serializers.DateField(required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class DeleteRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)
