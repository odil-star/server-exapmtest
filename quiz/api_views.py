from django.contrib.auth import authenticate
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import exception_handler

from .forms import TestUploadForm
from .models import Answer, Question, Test, TestBlock, TestResult, UserAnswer
from .parser import ParseQuestionsError, parse_test_file
from .serializers import (
    BlockDetailSerializer,
    TestBlockSerializer,
    TestDetailSerializer,
    TestListSerializer,
    TestResultDetailSerializer,
)
from .views import QUESTIONS_PER_BLOCK, _format_upload_error


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if (
        response is not None
        and response.status_code == status.HTTP_401_UNAUTHORIZED
        and getattr(exc, "default_code", "") == "not_authenticated"
    ):
        response.data = {
            "detail": "Authentication credentials were not provided.",
        }

    return response


@api_view(["POST"])
@permission_classes([AllowAny])
def api_login(request):
    print(request.data)

    username = request.data.get("username")
    password = request.data.get("password")

    if not username or not password:
        return Response(
            {"detail": "Введите логин и пароль."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(username=username, password=password)
    if user is None:
        return Response(
            {"detail": "Неверный логин или пароль."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    token, _created = Token.objects.get_or_create(user=user)
    return Response(
        {
            "token": token.key,
            "username": user.username,
            "is_staff": user.is_staff,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_logout(request):
    Token.objects.filter(user=request.user).delete()
    return Response({"detail": "Вы вышли из системы."})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_me(request):
    return Response(_user_payload(request.user))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_tests(request):
    tests = (
        Test.objects.filter(is_active=True)
        .annotate(block_count=Count("blocks"))
        .order_by("-created_at")
    )
    return Response(TestListSerializer(tests, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_test_detail(request, test_id):
    test = get_object_or_404(
        Test.objects.filter(is_active=True).prefetch_related("blocks__questions"),
        pk=test_id,
    )
    return Response(TestDetailSerializer(test).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_test_blocks(request, test_id):
    test = get_object_or_404(Test.objects.filter(is_active=True), pk=test_id)
    blocks = test.blocks.annotate(question_count=Count("questions")).order_by("order")
    return Response(TestBlockSerializer(blocks, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_block_detail(request, block_id):
    block = get_object_or_404(
        TestBlock.objects.select_related("test").prefetch_related("questions__answers"),
        pk=block_id,
        test__is_active=True,
    )
    return Response(BlockDetailSerializer(block).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_check_answer(request, block_id):
    block = get_object_or_404(TestBlock, pk=block_id, test__is_active=True)
    question_id = request.data.get("question_id")
    answer_id = request.data.get("answer_id")

    if question_id is None or answer_id is None:
        return Response(
            {"detail": "Нужно передать question_id и answer_id."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    question = get_object_or_404(Question, id=question_id, block=block)
    selected_answer = get_object_or_404(Answer, id=answer_id, question=question)
    correct_answer = question.answers.filter(is_correct=True).first()

    return Response(
        {
            "is_correct": selected_answer.is_correct,
            "selected_answer_id": selected_answer.id,
            "correct_answer_id": correct_answer.id if correct_answer else None,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_submit_block(request, block_id):
    block = get_object_or_404(
        TestBlock.objects.select_related("test").prefetch_related("questions__answers"),
        pk=block_id,
        test__is_active=True,
    )
    questions = list(block.questions.all())
    submitted_answers = request.data.get("answers", [])

    if not isinstance(submitted_answers, list):
        return Response(
            {"detail": "Поле answers должно быть списком."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    selected_by_question = {}
    for item in submitted_answers:
        question_id = item.get("question_id")
        answer_id = item.get("answer_id")
        if question_id is None or answer_id is None:
            return Response(
                {
                    "detail": (
                        "Каждый ответ должен содержать question_id и answer_id."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        selected_by_question[int(question_id)] = int(answer_id)

    question_ids = {question.id for question in questions}
    if set(selected_by_question) != question_ids:
        return Response(
            {"detail": "Нужно ответить на все вопросы блока."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    selected_answers = {}
    for question in questions:
        answer = get_object_or_404(
            Answer,
            id=selected_by_question[question.id],
            question=question,
        )
        selected_answers[question.id] = answer

    score = sum(1 for answer in selected_answers.values() if answer.is_correct)
    total = len(questions)
    percent = round((score / total) * 100) if total else 0

    with transaction.atomic():
        result = TestResult.objects.create(
            user=request.user,
            test=block.test,
            block=block,
            score=score,
            total_questions=total,
        )
        UserAnswer.objects.bulk_create(
            [
                UserAnswer(
                    result=result,
                    question=question,
                    selected_answer=selected_answers[question.id],
                    is_correct=selected_answers[question.id].is_correct,
                )
                for question in questions
            ]
        )

    return Response(
        {
            "result_id": result.id,
            "score": score,
            "total": total,
            "percent": percent,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_result_detail(request, result_id):
    queryset = TestResult.objects.select_related("block").prefetch_related(
        "user_answers__question__answers",
        "user_answers__selected_answer",
    )
    if not request.user.is_staff:
        queryset = queryset.filter(user=request.user)

    result = get_object_or_404(queryset, pk=result_id)
    return Response(TestResultDetailSerializer(result).data)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def api_admin_upload_test(request):
    form = TestUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)

    try:
        parsed_questions = parse_test_file(form.cleaned_data["file"])
    except (ParseQuestionsError, ValueError) as exc:
        return Response(
            {"file": [_format_upload_error(exc)]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        test = Test.objects.create(
            title=form.cleaned_data["title"],
            description=form.cleaned_data["description"],
        )
        for block_index, start in enumerate(
            range(0, len(parsed_questions), QUESTIONS_PER_BLOCK),
            start=1,
        ):
            block_questions = parsed_questions[start : start + QUESTIONS_PER_BLOCK]
            block = TestBlock.objects.create(
                test=test,
                title=f"Блок {block_index}",
                order=block_index,
            )
            for parsed_question in block_questions:
                question = Question.objects.create(
                    block=block,
                    text=parsed_question.text,
                )
                Answer.objects.bulk_create(
                    [
                        Answer(
                            question=question,
                            text=parsed_answer.text,
                            is_correct=parsed_answer.is_correct,
                        )
                        for parsed_answer in parsed_question.answers
                    ]
                )

    return Response(TestDetailSerializer(test).data, status=status.HTTP_201_CREATED)


def _user_payload(user):
    return {
        "is_authenticated": True,
        "username": user.username,
        "is_staff": user.is_staff,
    }
