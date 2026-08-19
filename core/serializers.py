from rest_framework import serializers

from accounts.models import User


class AgentChatSerializer(serializers.Serializer):
    message = serializers.CharField()

class ApproveEmailSerializer(serializers.Serializer):
    approved = serializers.BooleanField()

class RegisterationSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("username", "email", "password")
        extra_kwargs = {
            "password": {"write_only": True},
            "email" : {"required" : True}
        }


    def create(self, validated_data):
        return User.objects.create_user(**validated_data)
    
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
            user = User.objects.filter(email=email).first()
            if user is None or not user.check_password(password):
                raise serializers.ValidationError("Invalid email or password.")
        else:
            raise serializers.ValidationError("Both email and password are required.")

        data["user"] = user
        print("Validated user:", user)
        return data