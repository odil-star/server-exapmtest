import re
from dataclasses import dataclass
from pathlib import Path

from django.core.exceptions import ValidationError


@dataclass
class ParsedAnswer:
    text: str
    is_correct: bool


@dataclass
class ParsedQuestion:
    text: str
    answers: list[ParsedAnswer]


class ParseQuestionsError(ValueError):
    def __init__(
        self,
        message: str,
        question_number: int | None = None,
        question_text: str = "",
        answers: list[ParsedAnswer] | None = None,
        questions_found: int = 0,
    ):
        super().__init__(message)
        self.question_number = question_number
        self.question_text = question_text
        self.answers = answers or []
        self.questions_found = questions_found


def parse_test_file(uploaded_file) -> list[ParsedQuestion]:
    raw_text = read_uploaded_test_file(uploaded_file)
    return parse_questions(raw_text)


def read_uploaded_test_file(uploaded_file) -> str:
    extension = Path(uploaded_file.name.lower()).suffix

    if uploaded_file.size == 0:
        raise ValidationError("Отправленный файл пуст.")

    if extension == ".txt":
        return _read_txt_file(uploaded_file)

    if extension == ".docx":
        return _read_docx_file(uploaded_file)

    raise ValidationError("Можно загрузить только .txt или .docx файл")


def _read_txt_file(uploaded_file) -> str:
    content = uploaded_file.read()

    if not content.strip():
        raise ValidationError("Отправленный файл пуст.")

    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise ValidationError(
        "Не удалось прочитать файл. Сохраните .txt в кодировке UTF-8 или Windows-1251."
    )


def _read_docx_file(uploaded_file) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise ValidationError("Для загрузки .docx установите зависимость python-docx.") from exc

    try:
        document = Document(uploaded_file)
    except Exception as exc:
        raise ValidationError("Не удалось прочитать .docx файл. Проверьте, что файл не поврежден.") from exc

    text = "\n".join(
        paragraph.text.strip()
        for paragraph in document.paragraphs
        if paragraph.text.strip()
    )

    if not text.strip():
        raise ValidationError("Отправленный файл пуст.")

    return text


def parse_questions(text: str) -> list[ParsedQuestion]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    if not text.strip():
        raise ParseQuestionsError("Файл не содержит вопросов.", questions_found=0)

    raw_blocks = re.split(r"\n?\+{5,}\n?", text)
    questions: list[ParsedQuestion] = []

    for raw_block in raw_blocks:
        block = raw_block.strip()

        if not block:
            continue

        question_number = len(questions) + 1
        parts = [
            part.strip()
            for part in re.split(r"\n?={5,}\n?", block)
            if part.strip()
        ]

        if len(parts) < 3:
            raise ParseQuestionsError(
                f"Вопрос {question_number}: недостаточно данных.",
                question_number=question_number,
                questions_found=len(questions),
            )

        question_text = parts[0].strip()
        answer_parts = [_parse_answer_part(answer) for answer in parts[1:] if answer]
        answer_texts = [answer["text"] for answer in answer_parts if answer["text"]]

        if len(answer_texts) < 2:
            raise ParseQuestionsError(
                f"Вопрос {question_number}: должно быть минимум 2 варианта ответа.",
                question_number=question_number,
                question_text=question_text,
                questions_found=len(questions),
            )

        marked_answers = [
            answer
            for answer in answer_parts
            if answer["is_marked_correct"] and answer["text"]
        ]

        if len(marked_answers) > 1:
            raise ParseQuestionsError(
                f"Вопрос {question_number}: отмечено несколько правильных ответов.",
                question_number=question_number,
                question_text=question_text,
                answers=[
                    ParsedAnswer(
                        text=answer["text"],
                        is_correct=answer["is_marked_correct"],
                    )
                    for answer in answer_parts
                    if answer["text"]
                ],
                questions_found=len(questions),
            )

        has_marked_answer = bool(marked_answers)
        answers = [
            ParsedAnswer(
                text=answer["text"],
                is_correct=answer["is_marked_correct"]
                if has_marked_answer
                else index == 0,
            )
            for index, answer in enumerate(answer_parts)
            if answer["text"]
        ]

        questions.append(ParsedQuestion(text=question_text, answers=answers))

    if not questions:
        raise ParseQuestionsError("Файл не содержит вопросов.", questions_found=0)

    return questions


def _parse_answer_part(text: str) -> dict[str, str | bool]:
    stripped_text = text.strip()
    is_marked_correct = stripped_text.startswith("#")

    if is_marked_correct:
        stripped_text = stripped_text[1:].strip()

    return {
        "text": stripped_text,
        "is_marked_correct": is_marked_correct,
    }


def parse_test_text(raw_text: str) -> list[ParsedQuestion]:
    return parse_questions(raw_text)
