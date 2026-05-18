from django.conf import settings
from django.db import models


class Test(models.Model):
    title = models.CharField("Название", max_length=255)
    description = models.TextField("Описание", blank=True)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Тест"
        verbose_name_plural = "Тесты"

    def __str__(self):
        return self.title


class TestBlock(models.Model):
    test = models.ForeignKey(
        Test,
        on_delete=models.CASCADE,
        related_name="blocks",
        verbose_name="Тест",
    )
    title = models.CharField("Название блока", max_length=255)
    order = models.PositiveIntegerField("Порядок")
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        ordering = ["test", "order"]
        unique_together = ("test", "order")
        verbose_name = "Блок теста"
        verbose_name_plural = "Блоки тестов"

    def __str__(self):
        return f"{self.test} - {self.title}"


class Question(models.Model):
    block = models.ForeignKey(
        TestBlock,
        on_delete=models.CASCADE,
        related_name="questions",
        verbose_name="Блок",
    )
    text = models.TextField("Текст вопроса")

    class Meta:
        verbose_name = "Вопрос"
        verbose_name_plural = "Вопросы"

    def __str__(self):
        return self.text[:80]


class Answer(models.Model):
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="answers",
        verbose_name="Вопрос",
    )
    text = models.CharField("Текст ответа", max_length=500)
    is_correct = models.BooleanField("Правильный ответ", default=False)

    class Meta:
        verbose_name = "Ответ"
        verbose_name_plural = "Ответы"

    def __str__(self):
        return self.text


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="Пользователь",
    )
    nickname = models.CharField("Никнейм", max_length=50, blank=True)
    avatar_color = models.CharField("Цвет аватара", max_length=20, default="#2563eb")
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    updated_at = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        verbose_name = "Профиль пользователя"
        verbose_name_plural = "Профили пользователей"

    def __str__(self):
        return self.nickname or self.user.username


class TestResult(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="test_results",
        verbose_name="Пользователь",
    )
    test = models.ForeignKey(
        Test,
        on_delete=models.CASCADE,
        related_name="results",
        verbose_name="Тест",
    )
    block = models.ForeignKey(
        TestBlock,
        on_delete=models.CASCADE,
        related_name="results",
        verbose_name="Блок",
    )
    score = models.PositiveIntegerField("Правильных ответов")
    total_questions = models.PositiveIntegerField("Всего вопросов")
    created_at = models.DateTimeField("Дата прохождения", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Результат теста"
        verbose_name_plural = "Результаты тестов"

    def __str__(self):
        return f"{self.user} - {self.block}: {self.score}/{self.total_questions}"


class UserAnswer(models.Model):
    result = models.ForeignKey(
        TestResult,
        on_delete=models.CASCADE,
        related_name="user_answers",
        verbose_name="Результат",
    )
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        verbose_name="Вопрос",
    )
    selected_answer = models.ForeignKey(
        Answer,
        on_delete=models.CASCADE,
        verbose_name="Выбранный ответ",
    )
    is_correct = models.BooleanField("Ответ верный", default=False)

    class Meta:
        verbose_name = "Ответ пользователя"
        verbose_name_plural = "Ответы пользователей"

    def __str__(self):
        return f"{self.question} - {self.selected_answer}"
