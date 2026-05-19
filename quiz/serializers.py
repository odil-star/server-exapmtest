from rest_framework import serializers

from .models import Answer, Question, Test, TestBlock, TestResult


class TestListSerializer(serializers.ModelSerializer):
    block_count = serializers.SerializerMethodField()

    class Meta:
        model = Test
        fields = ("id", "title", "description", "created_at", "is_active", "block_count")

    def get_block_count(self, obj):
        return obj.blocks.count()


class TestBlockSerializer(serializers.ModelSerializer):
    question_count = serializers.SerializerMethodField()

    class Meta:
        model = TestBlock
        fields = ("id", "test", "title", "order", "created_at", "question_count")

    def get_question_count(self, obj):
        return obj.questions.count()


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
    answers = AnswerPublicSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = ("id", "text", "answers")


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
        result_answers = obj.user_answers.select_related(
            "question",
            "selected_answer",
        ).prefetch_related("question__answers")

        answers = []
        for user_answer in result_answers:
            correct_answer = user_answer.question.answers.filter(is_correct=True).first()
            answers.append(
                {
                    "question": user_answer.question.text,
                    "selected_answer": user_answer.selected_answer.text,
                    "correct_answer": correct_answer.text if correct_answer else "",
                    "is_correct": user_answer.is_correct,
                }
            )
        return answers
