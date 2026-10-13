from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import Question, Test, TestBlock, TestResult
from .parser import ParseQuestionsError, parse_questions
from .models import AnsweredQuestion
from .services import QUESTIONS_PER_BLOCK, create_test_from_parsed_questions, set_role

User = get_user_model()


def build_test_file(question_count):
    chunks = []
    for number in range(1, question_count + 1):
        chunks.append(
            f"+++++\nВопрос {number}\n======\nНеверно A\n======\n#Верно\n======\nНеверно B\n======\n"
        )
    return "".join(chunks)


class ParserTests(SimpleTestCase):
    def test_parses_marked_correct_answer(self):
        questions = parse_questions("+++++\nQ1\n======\nA\n======\n#B\n======\nC\n======\n")
        self.assertEqual(len(questions), 1)
        self.assertEqual([a.is_correct for a in questions[0].answers], [False, True, False])

    def test_first_answer_is_correct_when_nothing_marked(self):
        questions = parse_questions("+++++\nQ1\n======\nA\n======\nB\n======\n")
        self.assertEqual([a.is_correct for a in questions[0].answers], [True, False])

    def test_multiple_correct_answers_rejected(self):
        with self.assertRaises(ParseQuestionsError):
            parse_questions("+++++\nQ1\n======\n#A\n======\n#B\n======\n")

    def test_single_answer_rejected(self):
        with self.assertRaises(ParseQuestionsError):
            parse_questions("+++++\nQ1\n======\nA\n======\n")

    def test_empty_file_rejected(self):
        with self.assertRaises(ParseQuestionsError):
            parse_questions("   \n")


class ServicesTests(TestCase):
    def test_questions_are_split_into_blocks(self):
        parsed = parse_questions(build_test_file(QUESTIONS_PER_BLOCK + 3))
        test, blocks_count = create_test_from_parsed_questions("T", "", parsed)

        self.assertEqual(blocks_count, 2)
        sizes = [block.questions.count() for block in test.blocks.order_by("order")]
        self.assertEqual(sizes, [QUESTIONS_PER_BLOCK, 3])


class ApiTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user("student", password="pass12345")
        self.other = User.objects.create_user("other", password="pass12345")
        self.admin = User.objects.create_user("admin", password="pass12345", is_staff=True)
        self.superuser = User.objects.create_superuser("root", password="pass12345")

        parsed = parse_questions(build_test_file(30))
        self.test, _ = create_test_from_parsed_questions("Python", "desc", parsed)
        self.block = self.test.blocks.get(order=1)

    def login(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def full_answers(self, block, correct=True):
        answers = []
        for question in block.questions.prefetch_related("answers"):
            options = list(question.answers.all())
            chosen = next(a for a in options if a.is_correct == correct)
            answers.append({"question_id": question.id, "answer_id": chosen.id})
        return answers


class AuthTests(ApiTestCase):
    def test_login_returns_token(self):
        response = self.client.post(
            "/api/login/", {"username": "student", "password": "pass12345"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["token"])

    def test_login_wrong_password(self):
        response = self.client.post(
            "/api/login/", {"username": "student", "password": "nope"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_login_is_throttled(self):
        statuses = [
            self.client.post(
                "/api/login/", {"username": "student", "password": "nope"}, format="json"
            ).status_code
            for _ in range(12)
        ]
        self.assertIn(429, statuses)

    def test_protected_endpoint_requires_token(self):
        response = self.client.get("/api/tests/")
        self.assertEqual(response.status_code, 401)

    def test_health_is_public(self):
        self.assertEqual(self.client.get("/api/health/").status_code, 200)


class PermissionTests(ApiTestCase):
    def test_regular_user_cannot_upload(self):
        self.login(self.user)
        upload = SimpleUploadedFile("t.txt", build_test_file(2).encode("utf-8"))
        response = self.client.post(
            "/api/tests/upload/", {"title": "X", "file": upload}, format="multipart"
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_upload(self):
        self.login(self.admin)
        upload = SimpleUploadedFile("t.txt", build_test_file(2).encode("utf-8"))
        response = self.client.post(
            "/api/tests/upload/", {"title": "X", "file": upload}, format="multipart"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["questions_count"], 2)
        self.assertTrue(Test.objects.filter(title="X").exists())

    def test_admin_stats_superuser_only(self):
        self.login(self.admin)
        self.assertEqual(self.client.get("/api/admin/stats/").status_code, 403)
        self.login(self.superuser)
        self.assertEqual(self.client.get("/api/admin/stats/").status_code, 200)

    def test_result_visible_only_to_owner(self):
        set_role(self.user, "premium")
        self.login(self.user)
        submit = self.client.post(
            f"/api/blocks/{self.block.id}/submit/",
            {"answers": self.full_answers(self.block)},
            format="json",
        )
        result_id = submit.data["result_id"]

        self.assertEqual(self.client.get(f"/api/results/{result_id}/").status_code, 200)
        self.login(self.other)
        self.assertEqual(self.client.get(f"/api/results/{result_id}/").status_code, 404)

    def test_block_detail_hides_correct_flag(self):
        self.login(self.user)
        response = self.client.get(f"/api/blocks/{self.block.id}/")
        answer = response.data["questions"][0]["answers"][0]
        self.assertNotIn("is_correct", answer)


class SubmitBlockTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        set_role(self.user, "premium")
        self.login(self.user)
        self.url = f"/api/blocks/{self.block.id}/submit/"

    def test_perfect_score(self):
        response = self.client.post(
            self.url, {"answers": self.full_answers(self.block)}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["score"], QUESTIONS_PER_BLOCK)
        self.assertEqual(response.data["percent"], 100)
        self.assertFalse(TestResult.objects.get().is_random)

    def test_zero_score(self):
        response = self.client.post(
            self.url, {"answers": self.full_answers(self.block, correct=False)}, format="json"
        )
        self.assertEqual(response.data["score"], 0)

    def test_missing_questions_rejected(self):
        answers = self.full_answers(self.block)[:-1]
        response = self.client.post(self.url, {"answers": answers}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_non_numeric_ids_give_400_not_500(self):
        response = self.client.post(
            self.url,
            {"answers": [{"question_id": "abc", "answer_id": 1}]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_answers_must_be_list(self):
        response = self.client.post(self.url, {"answers": "oops"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_answer_from_other_question_rejected(self):
        answers = self.full_answers(self.block)
        answers[0]["answer_id"] = answers[1]["answer_id"]
        response = self.client.post(self.url, {"answers": answers}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(TestResult.objects.count(), 0)


class RandomTestTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        set_role(self.user, "premium")
        self.login(self.user)

    def test_random_block_respects_count(self):
        response = self.client.get(f"/api/tests/{self.test.id}/random-block/?count=10")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["question_count"], 10)
        ids = [q["id"] for q in response.data["questions"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_random_block_count_is_capped(self):
        response = self.client.get(f"/api/tests/{self.test.id}/random-block/?count=500")
        self.assertEqual(response.data["question_count"], 30)

    def test_random_submit_across_blocks_marks_is_random(self):
        first = self.full_answers(self.test.blocks.get(order=1))[:2]
        second = self.full_answers(self.test.blocks.get(order=2))[:2]
        response = self.client.post(
            "/api/random-test/submit/", {"answers": first + second}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 4)
        self.assertTrue(TestResult.objects.get().is_random)

    def test_random_submit_rejects_duplicates_and_garbage(self):
        answer = self.full_answers(self.block)[0]
        for payload in (
            {"answers": [answer, answer]},
            {"answers": []},
            {"answers": ["x"]},
            {"answers": [{"question_id": None, "answer_id": 1}]},
        ):
            response = self.client.post("/api/random-test/submit/", payload, format="json")
            self.assertEqual(response.status_code, 400, payload)

    def test_random_submit_unknown_question(self):
        response = self.client.post(
            "/api/random-test/submit/",
            {"answers": [{"question_id": 999999, "answer_id": 1}]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_check_answer(self):
        question = Question.objects.filter(block=self.block).first()
        correct = question.answers.get(is_correct=True)
        wrong = question.answers.filter(is_correct=False).first()

        ok = self.client.post(
            "/api/random-test/check-answer/",
            {"question_id": question.id, "answer_id": correct.id},
            format="json",
        )
        self.assertTrue(ok.data["is_correct"])

        bad = self.client.post(
            "/api/random-test/check-answer/",
            {"question_id": question.id, "answer_id": wrong.id},
            format="json",
        )
        self.assertFalse(bad.data["is_correct"])
        self.assertEqual(bad.data["correct_answer_id"], correct.id)


class LeaderboardTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        set_role(self.user, "premium")
        set_role(self.other, "premium")

    def submit(self, user, correct_count):
        self.login(user)
        answers = self.full_answers(self.block)
        wrong = self.full_answers(self.block, correct=False)
        mixed = answers[:correct_count] + wrong[correct_count:]
        response = self.client.post(
            f"/api/blocks/{self.block.id}/submit/", {"answers": mixed}, format="json"
        )
        self.assertEqual(response.status_code, 200)

    def test_best_result_per_user_and_ranking(self):
        self.submit(self.user, 5)
        self.submit(self.user, 20)  # лучший у student
        self.submit(self.other, 10)

        self.login(self.user)
        response = self.client.get("/api/leaderboard/")
        rows = response.data["results"]

        self.assertEqual([r["username"] for r in rows], ["student", "other"])
        self.assertEqual(rows[0]["score"], 20)
        self.assertEqual([r["rank"] for r in rows], [1, 2])

    def test_profile_rank_and_stats(self):
        self.submit(self.user, 5)
        self.submit(self.other, 10)

        self.login(self.user)
        profile = self.client.get("/api/profile/").data
        self.assertEqual(profile["leaderboard_rank"], 2)
        self.assertEqual(profile["tests_completed"], 1)

    def test_random_result_shown_as_random_test(self):
        self.login(self.user)
        answers = self.full_answers(self.block)[:3]
        self.client.post("/api/random-test/submit/", {"answers": answers}, format="json")

        row = self.client.get("/api/leaderboard/").data["results"][0]
        self.assertEqual(row["test_title"], "Случайный тест")


class QueryCountTests(ApiTestCase):
    def test_tests_list_has_constant_query_count(self):
        for index in range(5):
            create_test_from_parsed_questions(
                f"T{index}", "", parse_questions(build_test_file(3))
            )
        self.login(self.user)
        with self.assertNumQueries(2):  # токен (с пользователем) + список, без N+1
            self.client.get("/api/tests/")


class RegistrationTests(ApiTestCase):
    def register(self, **overrides):
        payload = {"username": "newbie", "password": "Str0ng-pass-2026"}
        payload.update(overrides)
        return self.client.post("/api/register/", payload, format="json")

    def test_register_creates_regular_user_and_returns_token(self):
        response = self.register(email="n@example.com")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["token"])
        self.assertEqual(response.data["role"], "user")
        user = User.objects.get(username="newbie")
        self.assertFalse(user.is_staff)
        self.assertTrue(user.check_password("Str0ng-pass-2026"))
        self.assertTrue(hasattr(user, "profile"))

    def test_new_token_works(self):
        token = self.register().data["token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(self.client.get("/api/me/").data["role"], "user")

    def test_role_cannot_be_chosen_on_register(self):
        response = self.register(role="admin", is_staff=True, is_superuser=True)
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(username="newbie")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(response.data["role"], "user")

    def test_duplicate_username_case_insensitive(self):
        self.assertEqual(self.register(username="STUDENT").status_code, 400)

    def test_weak_password_rejected(self):
        for password in ("123", "password", "12345678"):
            self.assertEqual(self.register(password=password).status_code, 400, password)

    def test_invalid_username_rejected(self):
        for username in ("ab", "bad name", "x" * 60):
            self.assertEqual(self.register(username=username).status_code, 400, username)

    def test_register_can_be_disabled(self):
        with self.settings(REGISTRATION_ENABLED=False):
            self.assertEqual(self.register().status_code, 403)

    def test_register_is_throttled(self):
        statuses = [self.register(username=f"user{i}").status_code for i in range(12)]
        self.assertIn(429, statuses)


class RolesTests(ApiTestCase):
    def role_url(self, user):
        return f"/api/admin/users/{user.id}/role/"

    def test_roles_in_me_and_login(self):
        self.login(self.user)
        self.assertEqual(self.client.get("/api/me/").data["role"], "user")
        self.login(self.admin)
        self.assertEqual(self.client.get("/api/me/").data["role"], "admin")
        self.login(self.superuser)
        self.assertEqual(self.client.get("/api/profile/").data["role"], "admin")

    def test_admin_grants_and_revokes_premium(self):
        self.login(self.admin)
        response = self.client.patch(self.role_url(self.user), {"role": "premium"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role"], "premium")

        self.login(self.user)
        self.assertEqual(self.client.get("/api/me/").data["role"], "premium")

        self.login(self.admin)
        self.client.patch(self.role_url(self.user), {"role": "user"}, format="json")
        self.login(self.user)
        self.assertEqual(self.client.get("/api/me/").data["role"], "user")

    def test_regular_and_premium_cannot_manage_roles(self):
        self.login(self.user)
        response = self.client.patch(self.role_url(self.other), {"role": "premium"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/admin/users/").status_code, 403)

    def test_staff_cannot_grant_admin_only_superuser(self):
        self.login(self.admin)
        response = self.client.patch(self.role_url(self.user), {"role": "admin"}, format="json")
        self.assertEqual(response.status_code, 403)

        self.login(self.superuser)
        response = self.client.patch(self.role_url(self.user), {"role": "admin"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_staff)

    def test_cannot_change_own_role_or_superuser(self):
        self.login(self.superuser)
        self.assertEqual(
            self.client.patch(self.role_url(self.superuser), {"role": "user"}, format="json").status_code,
            400,
        )
        other_super = User.objects.create_superuser("root2", password="pass12345")
        self.assertEqual(
            self.client.patch(self.role_url(other_super), {"role": "user"}, format="json").status_code,
            403,
        )

    def test_invalid_role_rejected(self):
        self.login(self.admin)
        response = self.client.patch(self.role_url(self.user), {"role": "god"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_users_list_for_admin(self):
        self.login(self.admin)
        response = self.client.get("/api/admin/users/")
        self.assertEqual(response.status_code, 200)
        roles = {row["username"]: row["role"] for row in response.data}
        self.assertEqual(roles["student"], "user")
        self.assertEqual(roles["admin"], "admin")


class FreeLimitTests(ApiTestCase):
    """Обычный пользователь: 5 ответов за все время, дальше нужен премиум от админа."""

    def setUp(self):
        super().setUp()
        self.login(self.user)
        self.questions = list(
            Question.objects.filter(block=self.block).prefetch_related("answers")
        )

    def check(self, question, correct=True):
        answer = next(a for a in question.answers.all() if a.is_correct == correct)
        return self.client.post(
            f"/api/blocks/{self.block.id}/check-answer/",
            {"question_id": question.id, "answer_id": answer.id},
            format="json",
        )

    def test_sixth_answer_is_blocked_with_402(self):
        for question in self.questions[:5]:
            response = self.check(question)
            self.assertEqual(response.status_code, 200)

        blocked = self.check(self.questions[5])
        self.assertEqual(blocked.status_code, 402)
        self.assertEqual(blocked.data["code"], "free_limit_reached")
        self.assertIn("администратор", blocked.data["detail"])
        self.assertEqual(AnsweredQuestion.objects.filter(user=self.user).count(), 5)

    def test_usage_counter_in_response(self):
        response = self.check(self.questions[0])
        self.assertEqual(response.data["free_answers_limit"], 5)
        self.assertEqual(response.data["free_answers_used"], 1)
        self.assertEqual(response.data["free_answers_left"], 4)

    def test_repeating_same_question_does_not_spend_limit(self):
        for _ in range(8):
            self.assertEqual(self.check(self.questions[0]).status_code, 200)
        self.assertEqual(AnsweredQuestion.objects.filter(user=self.user).count(), 1)

    def test_already_answered_questions_stay_available_after_limit(self):
        for question in self.questions[:5]:
            self.check(question)
        self.assertEqual(self.check(self.questions[0]).status_code, 200)

    def test_limit_is_across_blocks_and_random_test(self):
        for question in self.questions[:3]:
            self.check(question)
        block2_questions = list(
            Question.objects.filter(block__order=2, block__test=self.test).prefetch_related("answers")
        )
        for question in block2_questions[:2]:
            answer = next(a for a in question.answers.all() if a.is_correct)
            response = self.client.post(
                "/api/random-test/check-answer/",
                {"question_id": question.id, "answer_id": answer.id},
                format="json",
            )
            self.assertEqual(response.status_code, 200)

        extra = block2_questions[2]
        answer = next(a for a in extra.answers.all() if a.is_correct)
        response = self.client.post(
            "/api/random-test/check-answer/",
            {"question_id": extra.id, "answer_id": answer.id},
            format="json",
        )
        self.assertEqual(response.status_code, 402)

    def test_submit_of_answered_questions_works_and_counts(self):
        answers = []
        for question in self.questions[:5]:
            self.check(question)
            correct = next(a for a in question.answers.all() if a.is_correct)
            answers.append({"question_id": question.id, "answer_id": correct.id})

        response = self.client.post(
            f"/api/blocks/{self.block.id}/submit/", {"answers": answers}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 5)
        self.assertEqual(response.data["free_answers_left"], 0)

    def test_submit_over_limit_is_rejected_and_not_saved(self):
        response = self.client.post(
            f"/api/blocks/{self.block.id}/submit/",
            {"answers": self.full_answers(self.block)},
            format="json",
        )
        self.assertEqual(response.status_code, 402)
        self.assertEqual(TestResult.objects.count(), 0)
        self.assertEqual(AnsweredQuestion.objects.count(), 0)

    def test_random_submit_over_limit_is_rejected(self):
        response = self.client.post(
            "/api/random-test/submit/",
            {"answers": self.full_answers(self.block)[:6]},
            format="json",
        )
        self.assertEqual(response.status_code, 402)
        self.assertEqual(TestResult.objects.count(), 0)

    def test_premium_is_unlimited_after_admin_grants_it(self):
        for question in self.questions[:5]:
            self.check(question)
        self.assertEqual(self.check(self.questions[5]).status_code, 402)

        self.login(self.admin)
        self.client.patch(
            f"/api/admin/users/{self.user.id}/role/", {"role": "premium"}, format="json"
        )

        self.login(self.user)
        for question in self.questions[5:]:
            self.assertEqual(self.check(question).status_code, 200)
        me = self.client.get("/api/me/").data
        self.assertEqual(me["role"], "premium")
        self.assertIsNone(me["free_answers_limit"])

    def test_admin_is_unlimited(self):
        self.login(self.admin)
        for question in self.questions[:8]:
            self.assertEqual(self.check(question).status_code, 200)

    def test_me_and_profile_show_usage(self):
        self.check(self.questions[0])
        self.assertEqual(self.client.get("/api/me/").data["free_answers_left"], 4)
        self.assertEqual(self.client.get("/api/profile/").data["free_answers_used"], 1)


class PremiumUploadTests(ApiTestCase):
    def upload(self, questions=3, title="Мой тест"):
        file = SimpleUploadedFile("t.txt", build_test_file(questions).encode("utf-8"))
        return self.client.post(
            "/api/tests/upload/", {"title": title, "file": file}, format="multipart"
        )

    def test_regular_user_cannot_upload(self):
        self.login(self.user)
        response = self.upload()
        self.assertEqual(response.status_code, 403)
        self.assertIn("премиум", response.data["detail"])
        self.assertFalse(Test.objects.filter(title="Мой тест").exists())

    def test_premium_can_upload_and_is_author(self):
        set_role(self.user, "premium")
        self.login(self.user)
        response = self.upload(questions=30)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["questions_count"], 30)
        self.assertEqual(response.data["blocks_count"], 2)

        created = Test.objects.get(pk=response.data["test_id"])
        self.assertEqual(created.created_by, self.user)

    def test_uploaded_test_visible_to_everyone_with_author(self):
        set_role(self.user, "premium")
        self.login(self.user)
        self.upload(title="От премиума")

        self.login(self.other)
        rows = self.client.get("/api/tests/").data
        row = next(r for r in rows if r["title"] == "От премиума")
        self.assertEqual(row["author"], "student")

    def test_premium_upload_size_is_limited(self):
        set_role(self.user, "premium")
        self.login(self.user)
        with self.settings(PREMIUM_UPLOAD_MAX_QUESTIONS=5):
            response = self.upload(questions=6)
        self.assertEqual(response.status_code, 400)
        self.assertIn("максимум", response.data["detail"])
        self.assertFalse(Test.objects.filter(title="Мой тест").exists())

    def test_admin_upload_has_no_size_limit(self):
        self.login(self.admin)
        with self.settings(PREMIUM_UPLOAD_MAX_QUESTIONS=5):
            response = self.upload(questions=6)
        self.assertEqual(response.status_code, 201)

    def test_bad_file_gives_400_for_premium(self):
        set_role(self.user, "premium")
        self.login(self.user)
        bad = SimpleUploadedFile("t.txt", "просто текст".encode("utf-8"))
        response = self.client.post(
            "/api/tests/upload/", {"title": "X", "file": bad}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)

    def test_admin_users_cannot_be_downgraded_losing_upload_right(self):
        set_role(self.user, "premium")
        set_role(self.user, "user")
        self.login(self.user)
        self.assertEqual(self.upload().status_code, 403)
