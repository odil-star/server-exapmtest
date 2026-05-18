from django.urls import path

from . import api_views

urlpatterns = [
    path("login/", api_views.api_login, name="api_login"),
    path("logout/", api_views.api_logout, name="api_logout"),
    path("me/", api_views.api_me, name="api_me"),
    path("tests/", api_views.api_tests, name="api_tests"),
    path("tests/<int:test_id>/", api_views.api_test_detail, name="api_test_detail"),
    path("tests/<int:test_id>/blocks/", api_views.api_test_blocks, name="api_test_blocks"),
    path("blocks/<int:block_id>/", api_views.api_block_detail, name="api_block_detail"),
    path("blocks/<int:block_id>/check-answer/", api_views.api_check_answer, name="api_check_answer"),
    path("blocks/<int:block_id>/submit/", api_views.api_submit_block, name="api_submit_block"),
    path("results/<int:result_id>/", api_views.api_result_detail, name="api_result_detail"),
    path("admin/tests/upload/", api_views.api_admin_upload_test, name="api_admin_upload_test"),
]
