from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import IntegrityError
from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from .models import FileAttachment, Milestone, Notification, Project, Task, TimeLog, UserProfile
User = get_user_model()


#login serializer
class AuthLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        allowed_domain = getattr(settings, "ALLOWED_OFFICE_EMAIL_DOMAIN", "@apparatus.solutions")
        if not attrs["email"].lower().endswith(allowed_domain):
            raise serializers.ValidationError(f"Only office emails ending with {allowed_domain} are allowed.")
        user = authenticate(username=attrs["email"], password=attrs["password"])
        if not user:
            raise serializers.ValidationError("Invalid credentials.")
        attrs["user"] = user
        return attrs



#user serializer
class UserSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "first_name", "last_name", "email", "role", "status", "date_joined", "last_login"]
        read_only_fields = ["id", "date_joined", "last_login"]

    def get_role(self, obj) -> str:
        profile = getattr(obj, "profile", None)
        return getattr(profile, "role", None)

    def get_status(self, obj) -> str:
        profile = getattr(obj, "profile", None)
        return getattr(profile, "status", None)





#user create serializer
class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6)
    role = serializers.ChoiceField(choices=UserProfile.Roles.choices)
    status = serializers.ChoiceField(choices=UserProfile.Status.choices, default=UserProfile.Status.ACTIVE)

    class Meta:
        model = User
        fields = ["id", "first_name", "last_name", "email", "password", "role", "status"]
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

    def validate_role(self, value):
        if isinstance(value, str):
            upper = value.strip().upper()
            if upper in UserProfile.Roles.values:
                return upper
        return value

    def create(self, validated_data):
        role = validated_data.pop("role")
        profile_status = validated_data.pop("status", UserProfile.Status.ACTIVE)
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
        UserProfile.objects.create(user=user, role=role, status=profile_status)
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6, required=False, allow_blank=False)
    role = serializers.ChoiceField(choices=UserProfile.Roles.choices, required=False)
    status = serializers.ChoiceField(choices=UserProfile.Status.choices, required=False)

    class Meta:
        model = User
        fields = ["id", "first_name", "last_name", "email", "password", "role", "status"]
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
        if profile_changed_fields:
            profile.save(update_fields=profile_changed_fields)

        if password:
            instance.set_password(password)
            instance.save(update_fields=["password"])

        return instance



#project serializer
class ProjectSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField(read_only=True)

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
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]

    def get_created_by_name(self, obj) -> str:
        if not obj.created_by:
            return ""
        return obj.created_by.get_full_name().strip() or obj.created_by.email

    def validate(self, attrs):
        if self.instance and "deadline" in attrs and attrs["deadline"] != self.instance.deadline:
            request = self.context.get("request")
            role = getattr(getattr(getattr(request, "user", None), "profile", None), "role", None)
            if role != UserProfile.Roles.ADMIN:
                raise serializers.ValidationError({"deadline": "Only Admin can modify project deadline."})
        return attrs





#milestone serializer
class MilestoneSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField(read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)

    class Meta:
        model = Milestone
        fields = [
            "id",
            "milestone_no",
            "project",
            "project_name",
            "name",
            "start_date",
            "end_date",
            "status",
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

    def validate(self, attrs):
        if self.instance and "end_date" in attrs and attrs["end_date"] != self.instance.end_date:
            request = self.context.get("request")
            if not request or request.user.id != self.instance.created_by_id:
                raise serializers.ValidationError({"end_date": "Only milestone creator can modify end_date."})
        return attrs




#task serializer
class TaskSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField(read_only=True)
    assigned_to_name = serializers.SerializerMethodField(read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    milestone_name = serializers.CharField(source="milestone.name", read_only=True)

    class Meta:
        model = Task
        fields = [
            "id",
            "project",
            "project_name",
            "milestone",
            "milestone_name",
            "title",
            "description",
            "assigned_to",
            "assigned_to_name",
            "created_by",
            "created_by_name",
            "status",
            "priority",
            "deadline",
            "total_time_spent_seconds",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "total_time_spent_seconds", "created_at", "updated_at"]

    def get_created_by_name(self, obj) -> str:
        if not obj.created_by:
            return ""
        return obj.created_by.get_full_name().strip() or obj.created_by.email

    def get_assigned_to_name(self, obj) -> str:
        if not obj.assigned_to:
            return ""
        return obj.assigned_to.get_full_name().strip() or obj.assigned_to.email




#time log serializer
class TimeLogSerializer(serializers.ModelSerializer):
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
    class Meta:
        model = FileAttachment
        fields = "__all__"
        read_only_fields = ["id", "uploaded_by", "mime_type", "size_bytes", "created_at", "updated_at"]

    def validate_file(self, value):
        # Reject plain strings/URLs and enforce multipart binary uploads.
        if not isinstance(value, UploadedFile):
            raise serializers.ValidationError("Upload a valid file using multipart/form-data.")
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


class AdminPasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()
    new_password = serializers.CharField(min_length=6, write_only=True)


class AdminForgotPasswordRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class AdminForgotPasswordVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(min_length=6, max_length=6)
    new_password = serializers.CharField(min_length=6, write_only=True)


class DeadlineChangeRequestSerializer(serializers.Serializer):
    new_deadline = serializers.DateField(required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class DeleteRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)
