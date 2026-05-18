from django.urls import path

from . import views

urlpatterns = [
    path("", views.test_list, name="home"),
    path("tests/", views.test_list, name="test_list"),
    path("tests/<int:test_id>/", views.test_blocks, name="test_blocks"),
    path(
        "tests/<int:test_id>/block/<int:block_id>/",
        views.take_block,
        name="take_block",
    ),
    path(
        "tests/<int:test_id>/block/<int:block_id>/result/",
        views.block_result,
        name="block_result",
    ),
    path("admin-upload-test/", views.admin_upload_test, name="admin_upload_test"),
]
