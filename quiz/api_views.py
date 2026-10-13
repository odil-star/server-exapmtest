import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    parser_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import exception_handler

from .forms import TestUploadForm
from .models import (
    Question,
    Test,
    TestBlock,
    TestResult,
    UserActivity,
    UserProfile,
)
from .parser import ParseQuestionsError, parse_test_file
from .serializers import (
    BlockDetailSerializer,
    CheckAnswerSerializer,
    RegisterSerializer,
    RoleSerializer,
    SubmitAnswersSerializer,
    TestBlockSerializer,
    TestDetailSerializer,
    TestListSerializer,
    TestResultDetailSerializer,
)
from .services import (
    ROLE_ADMIN,
    ROLE_USER,
    FreeLimitReached,
    can_upload_tests,
    consume_free_answers,
    limit_payload,
    RANDOM_TEST_TITLE,
    best_results_by_user,
    create_result,
    create_test_from_parsed_questions,
    format_upload_error,
    get_role,
    leaderboard_rank_for_user,
    percent_of,
    set_role,
)

RANDOM_TEST_MAX_QUESTIONS = 50
LEADERBOARD_SIZE = 5


class LoginRateThrottle(AnonRateThrottle):
    scope = "login"


class RegisterRateThrottle(AnonRateThrottle):
    scope = "register"


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


def _bad_request(detail):
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


def _first_error(errors):
    """Достает первое сообщение об ошибке из вложенной структуры serializer.errors."""
    if isinstance(errors, dict):
        for value in errors.values():
            return _first_error(value)
    if isinstance(errors, (list, tuple)):
        for value in errors:
            return _first_error(value)
    return str(errors)


def _validated(serializer_class, data):
    """Возвращает (validated_data, None) или (None, 400-ответ)."""
    serializer = serializer_class(data=data)
    if not serializer.is_valid():
        return None, _bad_request(_first_error(serializer.errors))
    return serializer.validated_data, None


def _limit_response(exc):
    """402: бесплатный лимит исчерпан, нужен премиум (выдает администратор)."""
    return Response(
        {
            "detail": (
                f"Бесплатный лимит ({exc.limit} ответов) исчерпан. "
                "Чтобы продолжить, оформите премиум через администратора."
            ),
            "code": "free_limit_reached",
            "free_answers_limit": exc.limit,
            "free_answers_used": exc.used,
            "free_answers_left": 0,
        },
        status=status.HTTP_402_PAYMENT_REQUIRED,
    )


def _is_admin(user):
    return user.is_staff or user.is_superuser


@api_view(["GET"])
@permission_classes([AllowAny])
@authentication_classes([])
def api_health(request):
    return Response({"status": "ok"})


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def api_login(request):
    username = request.data.get("username")
    password = request.data.get("password")

    if not username or not password:
        return _bad_request("Введите логин и пароль.")

    user = authenticate(username=username, password=password)
    if user is None:
        return _bad_request("Неверный логин или пароль.")

    token, _created = Token.objects.get_or_create(user=user)
    _update_user_activity(user)
    return Response(
        {
            "token": token.key,
            **_user_payload(user, include_auth_flag=False),
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([RegisterRateThrottle])
def api_register(request):
    if not getattr(settings, "REGISTRATION_ENABLED", True):
        return Response(
            {"detail": "Регистрация отключена."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Роль в регистрации не принимается: новый аккаунт всегда обычный пользователь.
    payload, error = _validated(RegisterSerializer, request.data)
    if error:
        return error

    User = get_user_model()
    user = User.objects.create_user(
        username=payload["username"],
        email=payload.get("email", ""),
        password=payload["password"],
    )
    token, _created = Token.objects.get_or_create(user=user)
    _update_user_activity(user)
    return Response(
        {"token": token.key, **_user_payload(user, include_auth_flag=False)},
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_logout(request):
    Token.objects.filter(user=request.user).delete()
    return Response({"detail": "Вы вышли из системы."})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_me(request):
    _update_user_activity(request.user)
    return Response(_user_payload(request.user))


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def api_profile(request):
    profile, _created = UserProfile.objects.get_or_create(user=request.user)

    if request.method == "PATCH" and "nickname" in request.data:
        nickname = str(request.data.get("nickname", "")).strip()
        if not nickname:
            return _bad_request("Никнейм не может быть пустым.")
        if len(nickname) < 2:
            return _bad_request("Никнейм должен содержать минимум 2 символа.")
        if len(nickname) > 50:
            return _bad_request("Никнейм должен быть не длиннее 50 символов.")

        profile.nickname = nickname
        profile.save(update_fields=["nickname", "updated_at"])

    return Response(_profile_payload(request.user, profile))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_tests(request):
    tests = (
        Test.objects.filter(is_active=True)
        .select_related("created_by")
        .annotate(block_count=Count("blocks"))
        .order_by("-created_at")
    )
    return Response(TestListSerializer(tests, many=True).data)


def _upload_test(title, description, uploaded_file, created_by=None, max_questions=None):
    """Общая логика загрузки: (test, blocks_count, questions_count) или (None, None, ошибка)."""
    try:
        parsed_questions = parse_test_file(uploaded_file)
    except (ParseQuestionsError, ValidationError, ValueError) as exc:
        return None, None, format_upload_error(exc)

    if max_questions is not None and len(parsed_questions) > max_questions:
        return None, None, (
            f"В тесте {len(parsed_questions)} вопросов, максимум — {max_questions}."
        )

    test, blocks_count = create_test_from_parsed_questions(
        title=title,
        description=description,
        parsed_questions=parsed_questions,
        created_by=created_by,
    )
    return test, blocks_count, len(parsed_questions)


@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def api_upload_test(request):
    if not can_upload_tests(request.user):
        return Response(
            {
                "detail": (
                    "Нет доступа. Загружать тесты могут премиум-пользователи "
                    "и администраторы."
                )
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    title = str(request.data.get("title", "")).strip()
    description = str(request.data.get("description", "")).strip()
    uploaded_file = request.FILES.get("file")

    if not title:
        return _bad_request("Введите название теста.")
    if not uploaded_file:
        return _bad_request("Файл не отправлен.")

    # Для премиум-пользователей размер теста ограничен, администраторы без лимита.
    max_questions = None if _is_admin(request.user) else settings.PREMIUM_UPLOAD_MAX_QUESTIONS
    test, blocks_count, questions_count = _upload_test(
        title,
        description,
        uploaded_file,
        created_by=request.user,
        max_questions=max_questions,
    )
    if test is None:
        return _bad_request(questions_count)

    return Response(
        {
            "success": True,
            "test_id": test.id,
            "title": test.title,
            "questions_count": questions_count,
            "blocks_count": blocks_count,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([IsAdminUser])
def api_admin_upload_test(request):
    """Устаревший эндпоинт (form-based). Оставлен для совместимости с фронтом."""
    form = TestUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)

    test, _blocks_count, error = _upload_test(
        form.cleaned_data["title"],
        form.cleaned_data["description"],
        form.cleaned_data["file"],
        created_by=request.user,
    )
    if test is None:
        return Response({"file": [error]}, status=status.HTTP_400_BAD_REQUEST)

    return Response(TestDetailSerializer(test).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def api_admin_stats(request):
    if not request.user.is_superuser:
        return Response({"detail": "Нет доступа."}, status=status.HTTP_403_FORBIDDEN)

    online_since = timezone.now() - timedelta(minutes=5)
    User = get_user_model()

    return Response(
        {
            "users_total": User.objects.count(),
            "users_online": UserActivity.objects.filter(last_seen__gte=online_since).count(),
            "tests_total": Test.objects.count(),
            "questions_total": Question.objects.count(),
            "results_total": TestResult.objects.count(),
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_leaderboard(request):
    test_id = request.query_params.get("test_id")
    if test_id is not None:
        try:
            test_id = int(test_id)
        except ValueError:
            return Response(
                {"detail": "test_id должен быть числом."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    leaders = best_results_by_user(test_id)[:LEADERBOARD_SIZE]

    return Response(
        {
            "results": [
                {
                    "rank": index,
                    "username": result.user.username,
                    "percent": percent_of(result.score, result.total_questions),
                    "score": result.score,
                    "total": result.total_questions,
                    "test_title": RANDOM_TEST_TITLE if result.is_random else result.test.title,
                    "block_title": "" if result.is_random else result.block.title,
                    "completed_at": _format_completed_at(result.created_at),
                }
                for index, result in enumerate(leaders, start=1)
            ]
        }
    )


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


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_random_block(request, test_id):
    test = get_object_or_404(Test, id=test_id, is_active=True)

    try:
        count = int(request.GET.get("count", RANDOM_TEST_MAX_QUESTIONS))
    except ValueError:
        count = RANDOM_TEST_MAX_QUESTIONS
    count = max(1, min(count, RANDOM_TEST_MAX_QUESTIONS))

    # Выбираем id и сэмплируем в Python: ORDER BY RANDOM() сортирует всю таблицу.
    question_ids = list(Question.objects.filter(block__test=test).values_list("id", flat=True))
    picked_ids = random.sample(question_ids, min(count, len(question_ids)))
    questions = list(
        Question.objects.filter(id__in=picked_ids).prefetch_related("answers")
    )
    random.shuffle(questions)

    def shuffled_answers(question):
        answers = list(question.answers.all())
        random.shuffle(answers)
        return [{"id": answer.id, "text": answer.text} for answer in answers]

    return Response(
        {
            "id": f"random-{test.id}",
            "title": RANDOM_TEST_TITLE,
            "test_id": test.id,
            "question_count": len(questions),
            "questions": [
                {
                    "id": question.id,
                    "text": question.text,
                    "answers": shuffled_answers(question),
                }
                for question in questions
            ],
        }
    )


def _check_answer(user, question_queryset, data):
    payload, error = _validated(CheckAnswerSerializer, data)
    if error:
        return error

    question = get_object_or_404(
        question_queryset.prefetch_related("answers"),
        id=payload["question_id"],
    )
    answers = list(question.answers.all())
    selected_answer = next((a for a in answers if a.id == payload["answer_id"]), None)
    if selected_answer is None:
        return _bad_request("Вариант ответа не относится к этому вопросу.")
    correct_answer = next((a for a in answers if a.is_correct), None)

    try:
        consume_free_answers(user, [question.id])
    except FreeLimitReached as exc:
        return _limit_response(exc)

    return Response(
        {
            "is_correct": selected_answer.is_correct,
            "selected_answer_id": selected_answer.id,
            "correct_answer_id": correct_answer.id if correct_answer else None,
            **limit_payload(user),
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_random_check_answer(request):
    return _check_answer(
        request.user,
        Question.objects.filter(block__test__is_active=True),
        request.data,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_check_answer(request, block_id):
    block = get_object_or_404(TestBlock, pk=block_id, test__is_active=True)
    return _check_answer(request.user, Question.objects.filter(block=block), request.data)


def _resolve_selected_answers(questions, selected_by_question):
    """Сопоставляет выбранные answer_id с вопросами.

    Возвращает ([(question, answer), ...], None) или (None, текст ошибки).
    """
    pairs = []
    for question in questions:
        answer_id = selected_by_question[question.id]
        answer = next((a for a in question.answers.all() if a.id == answer_id), None)
        if answer is None:
            return None, "Вариант ответа не относится к вопросу."
        pairs.append((question, answer))
    return pairs, None


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_random_submit(request):
    payload, error = _validated(SubmitAnswersSerializer, request.data)
    if error:
        return error

    selected_by_question = {item["question_id"]: item["answer_id"] for item in payload["answers"]}
    questions_by_id = {
        question.id: question
        for question in Question.objects.filter(
            id__in=selected_by_question,
            block__test__is_active=True,
        )
        .select_related("block__test")
        .prefetch_related("answers")
    }

    if set(questions_by_id) != set(selected_by_question):
        return _bad_request("Один или несколько вопросов не найдены.")

    # Порядок как в запросе клиента.
    questions = [questions_by_id[item["question_id"]] for item in payload["answers"]]
    pairs, error_detail = _resolve_selected_answers(questions, selected_by_question)
    if error_detail:
        return _bad_request(error_detail)

    first_question = questions[0]
    try:
        with transaction.atomic():
            consume_free_answers(request.user, [question.id for question in questions])
            result = create_result(
                request.user,
                first_question.block.test,
                first_question.block,
                pairs,
                is_random=True,
            )
    except FreeLimitReached as exc:
        return _limit_response(exc)

    return Response(
        {
            "result_id": result.id,
            "score": result.score,
            "total": result.total_questions,
            "percent": percent_of(result.score, result.total_questions),
            "block_title": RANDOM_TEST_TITLE,
            **limit_payload(request.user),
            "answers": [
                {
                    "question": question.text,
                    "selected_answer": answer.text,
                    "correct_answer": next(
                        (a.text for a in question.answers.all() if a.is_correct), ""
                    ),
                    "is_correct": answer.is_correct,
                }
                for question, answer in pairs
            ],
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
    payload, error = _validated(SubmitAnswersSerializer, request.data)
    if error:
        return error

    questions = list(block.questions.all())
    selected_by_question = {item["question_id"]: item["answer_id"] for item in payload["answers"]}

    block_question_ids = {question.id for question in questions}
    if get_role(request.user) == ROLE_USER:
        # Обычный пользователь ограничен лимитом: засчитываются только отвеченные вопросы.
        if not set(selected_by_question) <= block_question_ids:
            return _bad_request("Ответы содержат вопросы не из этого блока.")
        questions = [q for q in questions if q.id in selected_by_question]
    elif set(selected_by_question) != block_question_ids:
        return _bad_request("Нужно ответить на все вопросы блока.")

    pairs, error_detail = _resolve_selected_answers(questions, selected_by_question)
    if error_detail:
        return _bad_request(error_detail)

    try:
        with transaction.atomic():
            consume_free_answers(request.user, [question.id for question in questions])
            result = create_result(request.user, block.test, block, pairs)
    except FreeLimitReached as exc:
        return _limit_response(exc)

    return Response(
        {
            "result_id": result.id,
            "score": result.score,
            "total": result.total_questions,
            "percent": percent_of(result.score, result.total_questions),
            **limit_payload(request.user),
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


@api_view(["GET"])
@permission_classes([IsAdminUser])
def api_admin_users(request):
    User = get_user_model()
    users = User.objects.select_related("profile").order_by("username")
    return Response(
        [
            {
                "id": user.id,
                "username": user.username,
                "email": user.email or "",
                "role": get_role(user),
                "is_superuser": user.is_superuser,
                "date_joined": _format_completed_at(user.date_joined),
            }
            for user in users
        ]
    )


@api_view(["PATCH"])
@permission_classes([IsAdminUser])
def api_admin_user_role(request, user_id):
    target = get_object_or_404(get_user_model(), pk=user_id)
    payload, error = _validated(RoleSerializer, request.data)
    if error:
        return error
    new_role = payload["role"]

    if target.pk == request.user.pk:
        return _bad_request("Нельзя изменить собственную роль.")
    if target.is_superuser:
        return Response(
            {"detail": "Роль суперпользователя изменить нельзя."},
            status=status.HTTP_403_FORBIDDEN,
        )
    # Назначать и снимать админов может только суперпользователь.
    if (new_role == ROLE_ADMIN or target.is_staff) and not request.user.is_superuser:
        return Response(
            {"detail": "Управлять администраторами может только суперпользователь."},
            status=status.HTTP_403_FORBIDDEN,
        )

    set_role(target, new_role)
    return Response({"id": target.id, "username": target.username, "role": get_role(target)})


def _update_user_activity(user):
    UserActivity.objects.update_or_create(
        user=user,
        defaults={"last_seen": timezone.now()},
    )


def _user_payload(user, include_auth_flag=True):
    payload = {
        "username": user.username,
        "role": get_role(user),
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        **limit_payload(user),
    }
    if include_auth_flag:
        payload["is_authenticated"] = True
    return payload


def _profile_payload(user, profile):
    totals = list(
        TestResult.objects.filter(user=user).values_list("score", "total_questions")
    )
    percents = [percent_of(score, total) for score, total in totals]

    return {
        "username": user.username,
        "role": get_role(user),
        "nickname": profile.nickname,
        "email": user.email or "",
        "date_joined": _format_completed_at(user.date_joined),
        "tests_completed": len(percents),
        "best_percent": max(percents) if percents else 0,
        "average_percent": round(sum(percents) / len(percents)) if percents else 0,
        "leaderboard_rank": leaderboard_rank_for_user(user.id),
        **limit_payload(user),
        "avatar_color": profile.avatar_color,
    }


def _format_completed_at(value):
    return value.isoformat().replace("+00:00", "Z")
