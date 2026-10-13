"""Бизнес-логика, общая для HTML-вьюх и REST API."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ExpressionWrapper, F, FloatField, Window
from django.db.models.functions import RowNumber

from .models import (
    Answer,
    AnsweredQuestion,
    Question,
    Test,
    TestBlock,
    TestResult,
    UserAnswer,
    UserProfile,
)

QUESTIONS_PER_BLOCK = 25
RANDOM_TEST_TITLE = "Случайный тест"


def format_upload_error(exc):
    if isinstance(exc, ValidationError):
        return " ".join(exc.messages)
    return str(exc)


@transaction.atomic
def create_test_from_parsed_questions(title, description, parsed_questions, created_by=None):
    """Создает тест и режет вопросы на блоки по QUESTIONS_PER_BLOCK. Возвращает (test, blocks_count)."""
    test = Test.objects.create(title=title, description=description, created_by=created_by)
    blocks_count = 0

    for block_index, start in enumerate(
        range(0, len(parsed_questions), QUESTIONS_PER_BLOCK),
        start=1,
    ):
        blocks_count = block_index
        block = TestBlock.objects.create(
            test=test,
            title=f"Блок {block_index}",
            order=block_index,
        )

        for parsed_question in parsed_questions[start : start + QUESTIONS_PER_BLOCK]:
            question = Question.objects.create(block=block, text=parsed_question.text)
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

    return test, blocks_count


@transaction.atomic
def create_result(user, test, block, selected_answers, is_random=False):
    """Сохраняет результат и ответы пользователя.

    selected_answers: список пар (question, answer) в порядке вопросов.
    """
    score = sum(1 for _question, answer in selected_answers if answer.is_correct)
    result = TestResult.objects.create(
        user=user,
        test=test,
        block=block,
        score=score,
        total_questions=len(selected_answers),
        is_random=is_random,
    )
    UserAnswer.objects.bulk_create(
        [
            UserAnswer(
                result=result,
                question=question,
                selected_answer=answer,
                is_correct=answer.is_correct,
            )
            for question, answer in selected_answers
        ]
    )
    return result


ROLE_USER = "user"
ROLE_PREMIUM = "premium"
ROLE_ADMIN = "admin"
ROLES = (ROLE_USER, ROLE_PREMIUM, ROLE_ADMIN)


def get_role(user):
    """Роль пользователя: admin (is_staff/is_superuser), premium (профиль) или user."""
    if user.is_staff or user.is_superuser:
        return ROLE_ADMIN
    profile = UserProfile.objects.filter(user=user).only("is_premium").first()
    if profile is not None and profile.is_premium:
        return ROLE_PREMIUM
    return ROLE_USER


def set_role(user, role):
    """Назначает роль. admin = is_staff; premium/user снимают is_staff и ставят is_premium."""
    profile, _created = UserProfile.objects.get_or_create(user=user)
    if role == ROLE_ADMIN:
        user.is_staff = True
    else:
        user.is_staff = False
        profile.is_premium = role == ROLE_PREMIUM
        profile.save(update_fields=["is_premium", "updated_at"])
    user.save(update_fields=["is_staff"])


def can_upload_tests(user):
    """Загружать тесты могут премиум-пользователи и администраторы."""
    return get_role(user) in (ROLE_PREMIUM, ROLE_ADMIN)


def percent_of(score, total):
    return round((score / total) * 100) if total else 0


def best_results_by_user(test_id=None):
    """Лучший результат каждого пользователя, отсортированный по рейтингу.

    Лучший = максимум процента, затем баллов, затем более раннее прохождение.
    Если передан test_id — учитываются только результаты этого теста.
    Все считается в БД (оконная функция), в Python попадают только выбранные строки.
    """
    ratio = ExpressionWrapper(
        F("score") * 1.0 / F("total_questions"),
        output_field=FloatField(),
    )
    order = [ratio.desc(), F("score").desc(), F("created_at").asc(), F("id").asc()]

    results = TestResult.objects.filter(total_questions__gt=0)
    if test_id is not None:
        results = results.filter(test_id=test_id)

    return (
        results.annotate(
            rank_in_user=Window(
                expression=RowNumber(),
                partition_by=[F("user_id")],
                order_by=order,
            )
        )
        .filter(rank_in_user=1)
        .select_related("user", "test", "block")
        .order_by(*order)
    )


def leaderboard_rank_for_user(user_id):
    ids = list(best_results_by_user().values_list("user_id", flat=True))
    try:
        return ids.index(user_id) + 1
    except ValueError:
        return None


class FreeLimitReached(Exception):
    def __init__(self, used, limit):
        super().__init__("free answer limit reached")
        self.used = used
        self.limit = limit


def free_answer_limit():
    return settings.FREE_ANSWER_LIMIT


def free_usage(user):
    """Сколько вопросов потратил бесплатный лимит."""
    return AnsweredQuestion.objects.filter(user=user).count()


def limit_payload(user, role=None):
    """Данные о лимите для фронта. Для premium/admin лимита нет (None)."""
    role = role or get_role(user)
    if role != ROLE_USER:
        return {"free_answers_limit": None, "free_answers_used": None, "free_answers_left": None}
    limit = free_answer_limit()
    used = free_usage(user)
    return {
        "free_answers_limit": limit,
        "free_answers_used": used,
        "free_answers_left": max(limit - used, 0),
    }


def consume_free_answers(user, question_ids):
    """Списывает бесплатный лимит у обычного пользователя.

    Новые вопросы регистрируются, уже отвеченные лимит не тратят. Если лимита не хватает,
    бросает FreeLimitReached и ничего не записывает. premium/admin лимитом не ограничены.
    """
    if get_role(user) != ROLE_USER:
        return

    limit = free_answer_limit()
    with transaction.atomic():
        profile, _created = UserProfile.objects.get_or_create(user=user)
        # Блокируем строку профиля, чтобы параллельные запросы не обошли лимит.
        UserProfile.objects.select_for_update().get(pk=profile.pk)

        requested = set(question_ids)
        already = set(
            AnsweredQuestion.objects.filter(user=user, question_id__in=requested).values_list(
                "question_id", flat=True
            )
        )
        new_ids = requested - already
        used = AnsweredQuestion.objects.filter(user=user).count()

        if used + len(new_ids) > limit:
            raise FreeLimitReached(used=used, limit=limit)

        AnsweredQuestion.objects.bulk_create(
            [AnsweredQuestion(user=user, question_id=question_id) for question_id in new_ids]
        )
