from django.db import migrations, models
import django.db.models.deletion


def split_existing_questions_into_blocks(apps, schema_editor):
    Test = apps.get_model("quiz", "Test")
    TestBlock = apps.get_model("quiz", "TestBlock")
    Question = apps.get_model("quiz", "Question")
    TestResult = apps.get_model("quiz", "TestResult")

    for test in Test.objects.all():
        questions = list(Question.objects.filter(test=test).order_by("id"))
        if not questions:
            block = TestBlock.objects.create(test=test, title="Блок 1", order=1)
            TestResult.objects.filter(test=test, block__isnull=True).update(block=block)
            continue

        created_blocks = []
        for index in range(0, len(questions), 25):
            order = index // 25 + 1
            block = TestBlock.objects.create(test=test, title=f"Блок {order}", order=order)
            created_blocks.append(block)
            for question in questions[index : index + 25]:
                question.block = block
                question.save(update_fields=["block"])

        TestResult.objects.filter(test=test, block__isnull=True).update(block=created_blocks[0])


class Migration(migrations.Migration):

    dependencies = [
        ("quiz", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TestBlock",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255, verbose_name="Название блока")),
                ("order", models.PositiveIntegerField(verbose_name="Порядок")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")),
                (
                    "test",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="blocks",
                        to="quiz.test",
                        verbose_name="Тест",
                    ),
                ),
            ],
            options={
                "verbose_name": "Блок теста",
                "verbose_name_plural": "Блоки тестов",
                "ordering": ["test", "order"],
                "unique_together": {("test", "order")},
            },
        ),
        migrations.AddField(
            model_name="question",
            name="block",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="questions",
                to="quiz.testblock",
                verbose_name="Блок",
            ),
        ),
        migrations.AddField(
            model_name="testresult",
            name="block",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="results",
                to="quiz.testblock",
                verbose_name="Блок",
            ),
        ),
        migrations.RunPython(split_existing_questions_into_blocks, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="question",
            name="block",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="questions",
                to="quiz.testblock",
                verbose_name="Блок",
            ),
        ),
        migrations.AlterField(
            model_name="testresult",
            name="block",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="results",
                to="quiz.testblock",
                verbose_name="Блок",
            ),
        ),
        migrations.RemoveField(
            model_name="question",
            name="test",
        ),
    ]
