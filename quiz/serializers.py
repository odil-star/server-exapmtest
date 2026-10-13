import random

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Answer, Question, Test, TestBlock, TestResult
from .services import ROLES


class TestListSerializer(serializers.ModelSerializer):
    block_count = serializers.SerializerMethodField()
    author = serializers.SerializerMethodField()

    class Meta:
        model = Test
        fields = ("id", "title", "description", "created_at", "is_active", "block_count", "author")

    def get_author(self, obj):
        return obj.created_by.username if obj.created_by_id else None

    def get_block_count(self, obj):
        annotated = getattr(obj, "block_count", None)
        return annotated if annotated is not None else obj.blocks.count()


class TestBlockSerializer(serializers.ModelSerializer):
    question_count = serializers.SerializerMethodField()

    class Meta:
        model = TestBlock
        fields = ("id", "test", "title", "order", "created_at", "question_count")

    def get_question_count(self, obj):
        annotated = getattr(obj, "question_count", None)
        return annotated if annotated is not None else obj.questions.count()


class TestDetailSerializer(serializers.ModelSerializer):
    blocks = TestBlockSerializer(many=True, read_only=True)

    class Meta:
        model = Test
        fields = ("id", "title", "description", "created_at", "is_active", "blocks")


class AnswerPublicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Answer
        fields = ("id", "text")


class QuestionPublicSerializer(serializers.ModelSerializer):
    answers = serializers.SerializerMethodField()

    class Meta:
        model = Question
        fields = ("id", "text", "answers")

    def get_answers(self, obj):
        # Варианты перемешиваются при каждой выдаче, чтобы порядок из файла
        # не выдавал правильный ответ.
        answers = list(obj.answers.all())
        random.shuffle(answers)
        return AnswerPublicSerializer(answers, many=True).data


class BlockDetailSerializer(serializers.ModelSerializer):
    questions = QuestionPublicSerializer(many=True, read_only=True)
    test_title = serializers.CharField(source="test.title", read_only=True)

    class Meta:
        model = TestBlock
        fields = ("id", "test", "test_title", "title", "order", "questions")


class TestResultDetailSerializer(serializers.ModelSerializer):
    total = serializers.IntegerField(source="total_questions", read_only=True)
    percent = serializers.SerializerMethodField()
    block_title = serializers.CharField(source="block.title", read_only=True)
    answers = serializers.SerializerMethodField()

    class Meta:
        model = TestResult
        fields = ("id", "score", "total", "percent", "block_title", "answers")

    def get_percent(self, obj):
        if not obj.total_questions:
            return 0
        return round((obj.score / obj.total_questions) * 100)

    def get_answers(self, obj):
        answers = []
        for user_answer in obj.user_answers.all():
            correct_answer = next(
                (answer for answer in user_answer.question.answers.all() if answer.is_correct),
                None,
            )
            answers.append(
                {
                    "question": user_answer.question.text,
                    "selected_answer": user_answer.selected_answer.text,
                    "correct_answer": correct_answer.text if correct_answer else "",
                    "is_correct": user_answer.is_correct,
                }
            )
        return answers


class SubmittedAnswerSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    answer_id = serializers.IntegerField()


class CheckAnswerSerializer(SubmittedAnswerSerializer):
    pass


class SubmitAnswersSerializer(serializers.Serializer):
    MAX_ANSWERS = 50

    answers = SubmittedAnswerSerializer(many=True, allow_empty=False, max_length=MAX_ANSWERS)

    def validate_answers(self, value):
        question_ids = [item["question_id"] for item in value]
        if len(set(question_ids)) != len(question_ids):
            raise serializers.ValidationError("Ответы на один вопрос переданы несколько раз.")
        return value


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(min_length=3, max_length=50)
    password = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False)
    email = serializers.EmailField(required=False, allow_blank=True, max_length=254)

    def validate_username(self, value):
        value = value.strip()
        try:
            UnicodeUsernameValidator()(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages[0])
        if get_user_model().objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Пользователь с таким логином уже существует.")
        return value

    def validate(self, attrs):
        candidate = get_user_model()(username=attrs["username"], email=attrs.get("email", ""))
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages})
        return attrs


class RoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ROLES)
