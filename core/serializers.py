from rest_framework import serializers

from accounts.models import User
from accounts.utils import (
    generate_recovery_otp,
    generate_secure_password,
    hash_recovery_otp,
    verify_recovery_otp,
)


class AgentChatSerializer(serializers.Serializer):
    message = serializers.CharField()


class ApproveEmailSerializer(serializers.Serializer):
    approved = serializers.BooleanField()


class RegisterationSerializer(serializers.ModelSerializer):
    recovery_code = serializers.CharField(read_only=True)
    auto_generate_password = serializers.BooleanField(required=False, default=False, write_only=True)

    class Meta:
        model = User
        fields = ("username", "email", "password", "recovery_code", "auto_generate_password")
        extra_kwargs = {
            "password": {"write_only": True, "required": False},
            "email": {"required": True},
        }

    def validate(self, data):
        auto_gen = data.get("auto_generate_password", False)
        password = data.get("password")
        if not auto_gen and not password:
            raise serializers.ValidationError({"password": "Password is required when not auto-generating."})
        return data

    def create(self, validated_data):
        auto_gen = validated_data.pop("auto_generate_password", False)
        password = validated_data.pop("password", None)
        
        generated_password = None
        if auto_gen or not password:
            generated_password = generate_secure_password()
            password = generated_password

        # Generate initial recovery OTP credential
        raw_recovery_otp = generate_recovery_otp()
        hashed_otp = hash_recovery_otp(raw_recovery_otp)

        user = User.objects.create_user(
            username=validated_data["username"],
            email=validated_data["email"],
            password=password,
            otp_secret=hashed_otp,
        )

        user._raw_recovery_code = raw_recovery_otp
        if generated_password:
            user._generated_password = generated_password

        return user

    def to_representation(self, instance):
        ret = {
            "id": instance.id,
            "username": instance.username,
            "email": instance.email,
            "recovery_code": getattr(instance, "_raw_recovery_code", None),
        }
        if hasattr(instance, "_generated_password") and instance._generated_password:
            ret["generated_password"] = instance._generated_password
        return ret


class LoginSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("email", "password")
        extra_kwargs = {
            "password": {"write_only": True}
        }

    def validate(self, data):
        email = data.get("email")
        password = data.get("password")

        if email and password:
            user = User.objects.filter(email__iexact=email).first()
            if user is None or not user.check_password(password):
                raise serializers.ValidationError("Invalid email or password.")
        else:
            raise serializers.ValidationError("Both email and password are required.")

        data["user"] = user
        return data


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

    def validate_email(self, value):
        user = User.objects.filter(email__iexact=value).first()
        if not user:
            raise serializers.ValidationError("User with this email does not exist.")
        return value


class VerifyOTPSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    otp = serializers.CharField(required=True, max_length=64)

    def validate(self, data):
        email = data.get("email")
        otp = data.get("otp")

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise serializers.ValidationError({"email": "User with this email does not exist."})

        if not user.otp_secret or not verify_recovery_otp(otp, user.otp_secret):
            raise serializers.ValidationError({"otp": "Invalid or expired recovery OTP/credential."})

        data["user"] = user
        return data


class ResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    otp = serializers.CharField(required=True, max_length=64)
    new_password = serializers.CharField(required=True, min_length=8, write_only=True)
    confirm_password = serializers.CharField(required=False, min_length=8, write_only=True)

    def validate(self, data):
        email = data.get("email")
        otp = data.get("otp")
        new_password = data.get("new_password")
        confirm_password = data.get("confirm_password")

        if confirm_password and new_password != confirm_password:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise serializers.ValidationError({"email": "User with this email does not exist."})

        if not user.otp_secret or not verify_recovery_otp(otp, user.otp_secret):
            raise serializers.ValidationError({"otp": "Invalid or expired recovery OTP/credential."})

        data["user"] = user
        return data


class OTPGenerateSerializer(serializers.Serializer):
    pass