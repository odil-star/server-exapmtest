from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from .forms import TestUploadForm
from .models import Answer, Question, Test, TestBlock, TestResult, UserAnswer
from .parser import ParseQuestionsError, ParsedAnswer, ParsedQuestion, parse_test_file

QUESTIONS_PER_BLOCK = 25
UPLOAD_PREVIEW_SESSION_KEY = "upload_test_preview"


def test_list(request):
    tests = Test.objects.filter(is_active=True).prefetch_related("blocks__questions")
    return render(request, "quiz/test_list.html", {"tests": tests})


@login_required
def test_blocks(request, test_id):
    test = get_object_or_404(
        Test.objects.filter(is_active=True).prefetch_related("blocks__questions"),
        pk=test_id,
    )
    blocks = test.blocks.all()
    return render(request, "quiz/test_blocks.html", {"test": test, "blocks": blocks})


@login_required
def take_block(request, test_id, block_id):
    test = get_object_or_404(Test.objects.filter(is_active=True), pk=test_id)
    block = get_object_or_404(
        TestBlock.objects.filter(test=test).prefetch_related("questions__answers"),
        pk=block_id,
    )
    questions = list(block.questions.all())

    if request.method == "POST":
        missing_questions = []
        selected_answers = {}

        for question in questions:
            answer_id = request.POST.get(f"question_{question.id}")
            if not answer_id:
                missing_questions.append(question)
                continue

            selected_answers[question.id] = get_object_or_404(
                Answer,
                id=answer_id,
                question=question,
            )

        if missing_questions:
            messages.error(request, "Ответьте на все вопросы перед завершением блока.")
            return render(
                request,
                "quiz/take_test.html",
                {"test": test, "block": block, "questions": questions},
            )

        score = sum(1 for answer in selected_answers.values() if answer.is_correct)

        with transaction.atomic():
            result = TestResult.objects.create(
                user=request.user,
                test=test,
                block=block,
                score=score,
                total_questions=len(questions),
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

        return redirect("block_result", test_id=test.pk, block_id=block.pk)

    return render(
        request,
        "quiz/take_test.html",
        {"test": test, "block": block, "questions": questions},
    )


@login_required
def block_result(request, test_id, block_id):
    test = get_object_or_404(Test, pk=test_id)
    block = get_object_or_404(TestBlock, pk=block_id, test=test)
    result = (
        TestResult.objects.filter(user=request.user, test=test, block=block)
        .prefetch_related("user_answers__question", "user_answers__selected_answer")
        .first()
    )

    if result is None:
        messages.info(request, "Сначала пройдите блок, чтобы увидеть результат.")
        return redirect("take_block", test_id=test.pk, block_id=block.pk)

    return render(request, "quiz/result.html", {"test": test, "block": block, "result": result})


@staff_member_required
def admin_upload_test(request):
    if request.method == "POST" and request.POST.get("action") == "save_preview":
        return _save_upload_preview(request)

    if request.method == "POST":
        form = TestUploadForm(request.POST, request.FILES)
        request.session.pop(UPLOAD_PREVIEW_SESSION_KEY, None)

        if "file" not in request.FILES:
            error = 'Файл не был отправлен. Проверьте enctype="multipart/form-data".'
            form.add_error("file", error)
            messages.error(request, error)
            return render(request, "quiz/admin_upload_test.html", {"form": form})

        if form.is_valid():
            try:
                parsed_questions = parse_test_file(form.cleaned_data["file"])
            except (ValidationError, ValueError) as exc:
                error = _format_upload_error(exc)
                form.add_error("file", error)
                messages.error(request, error)
                return render(
                    request,
                    "quiz/admin_upload_test.html",
                    {
                        "form": form,
                        "preview_error": _build_preview_error(exc),
                    },
                )

            preview = {
                "title": form.cleaned_data["title"],
                "description": form.cleaned_data["description"],
                "questions": _questions_to_dicts(parsed_questions),
            }
            request.session[UPLOAD_PREVIEW_SESSION_KEY] = preview
            messages.info(
                request,
                f"Найдено вопросов: {len(parsed_questions)}. Проверьте данные и сохраните тест.",
            )
            return render(
                request,
                "quiz/admin_upload_test.html",
                {
                    "form": form,
                    "preview": preview,
                    "question_count": len(parsed_questions),
                    "block_count": _count_blocks(len(parsed_questions)),
                },
            )
    else:
        form = TestUploadForm()

    return render(request, "quiz/admin_upload_test.html", {"form": form})


def _save_upload_preview(request):
    preview = request.session.get(UPLOAD_PREVIEW_SESSION_KEY)

    if not preview:
        messages.error(request, "Нет данных для сохранения. Сначала загрузите файл для предпросмотра.")
        return redirect("admin_upload_test")

    parsed_questions = _dicts_to_questions(preview["questions"])

    with transaction.atomic():
        test = Test.objects.create(
            title=preview["title"],
            description=preview["description"],
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

    request.session.pop(UPLOAD_PREVIEW_SESSION_KEY, None)
    messages.success(request, "Тест успешно сохранен и разбит на блоки.")
    return redirect("test_blocks", test_id=test.pk)


def _questions_to_dicts(questions):
    return [
        {
            "text": question.text,
            "answers": [
                {"text": answer.text, "is_correct": answer.is_correct}
                for answer in question.answers
            ],
        }
        for question in questions
    ]


def _dicts_to_questions(question_dicts):
    return [
        ParsedQuestion(
            text=question["text"],
            answers=[
                ParsedAnswer(text=answer["text"], is_correct=answer["is_correct"])
                for answer in question["answers"]
            ],
        )
        for question in question_dicts
    ]


def _build_preview_error(exc):
    if not isinstance(exc, ParseQuestionsError):
        return None

    return {
        "message": str(exc),
        "question_number": exc.question_number,
        "question_text": exc.question_text,
        "answers": exc.answers,
        "questions_found": exc.questions_found,
    }


def _count_blocks(question_count):
    return (question_count + QUESTIONS_PER_BLOCK - 1) // QUESTIONS_PER_BLOCK


def _format_upload_error(exc):
    if isinstance(exc, ValidationError):
        return " ".join(exc.messages)
    return str(exc)
