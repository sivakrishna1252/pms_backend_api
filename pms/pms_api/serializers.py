from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import IntegrityError
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
            raise serializers.ValidationError({"deadline": "Project deadline cannot be modified once created."})
        return attrs





#milestone serializer
class MilestoneSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField(read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)

    class Meta:
        model = Milestone
        fields = [
            "id",
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
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]

    def get_created_by_name(self, obj) -> str:
        if not obj.created_by:
            return ""
        return obj.created_by.get_full_name().strip() or obj.created_by.email

    def validate(self, attrs):
        if self.instance and "end_date" in attrs and attrs["end_date"] != self.instance.end_date:
            raise serializers.ValidationError({"end_date": "Milestone end_date cannot be modified once created."})
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


class AssignTaskSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()


class TaskStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Task.Status.choices)


class EmptySerializer(serializers.Serializer):
    pass


class FileUploadRequestSerializer(serializers.Serializer):
    task = serializers.IntegerField(required=False, allow_null=True)
    file = serializers.FileField()




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
