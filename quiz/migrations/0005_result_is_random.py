from django.conf import settings
from django.db import migrations, models
from django.db.models import Count


def mark_existing_random_results(apps, schema_editor):
    """Результаты, ответы которых охватывают несколько блоков, были случайными тестами."""
    TestResult = apps.get_model("quiz", "TestResult")
    random_ids = (
        TestResult.objects.annotate(block_count=Count("user_answers__question__block", distinct=True))
        .filter(block_count__gt=1)
        .values_list("id", flat=True)
    )
    TestResult.objects.filter(id__in=list(random_ids)).update(is_random=True)


class Migration(migrations.Migration):

    dependencies = [
        ("quiz", "0004_useractivity"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="testresult",
            name="is_random",
            field=models.BooleanField(default=False, verbose_name="Случайный тест"),
        ),
        migrations.AddIndex(
            model_name="testresult",
            index=models.Index(fields=["user", "-created_at"], name="result_user_created_idx"),
        ),
        migrations.RunPython(mark_existing_random_results, migrations.RunPython.noop),
    ]
