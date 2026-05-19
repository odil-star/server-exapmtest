from django.contrib import admin

from .models import Answer, Question, Test, TestBlock, TestResult, UserAnswer


class AnswerInline(admin.TabularInline):
    model = Answer
    extra = 2


class QuestionInline(admin.StackedInline):
    model = Question
    extra = 0


class TestBlockInline(admin.TabularInline):
    model = TestBlock
    extra = 0
    fields = ("title", "order", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ("title", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("title", "description")
    inlines = [TestBlockInline]


@admin.register(TestBlock)
class TestBlockAdmin(admin.ModelAdmin):
    list_display = ("title", "test", "order", "question_count", "created_at")
    list_filter = ("test", "created_at")
    search_fields = ("title", "test__title")
    inlines = [QuestionInline]

    def question_count(self, obj):
        return obj.questions.count()

    question_count.short_description = "Вопросов"


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("text", "block", "test")
    list_filter = ("block__test", "block")
    search_fields = ("text", "block__title", "block__test__title")
    inlines = [AnswerInline]

    def test(self, obj):
        return obj.block.test

    test.short_description = "Тест"


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ("text", "question", "is_correct")
    list_filter = ("is_correct", "question__block__test")
    search_fields = ("text",)


class UserAnswerInline(admin.TabularInline):
    model = UserAnswer
    extra = 0
    readonly_fields = ("question", "selected_answer", "is_correct")
    can_delete = False


@admin.register(TestResult)
class TestResultAdmin(admin.ModelAdmin):
    list_display = ("user", "test", "block", "score", "total_questions", "created_at")
    list_filter = ("test", "block", "created_at")
    search_fields = ("user__username", "test__title", "block__title")
    readonly_fields = ("user", "test", "block", "score", "total_questions", "created_at")
    inlines = [UserAnswerInline]


@admin.register(UserAnswer)
class UserAnswerAdmin(admin.ModelAdmin):
    list_display = ("result", "question", "selected_answer", "is_correct")
    list_filter = ("is_correct", "result__test", "result__block")
