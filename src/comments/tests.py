import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.services import IdentityService
from comments.schemas import CommentCreateSchema
from comments.service import CommentService
from comments.templatetags.comment_tags import filesize
from todos.schemas import TodoTitleSchema
from todos.services import TodoService


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AttachmentTests(TestCase):
    def setUp(self):
        self.owner = IdentityService.create_user("a@x.com")
        todo = TodoService.create(self.owner, TodoTitleSchema(title="t"))
        upload = SimpleUploadedFile("shot.png", b"\x89PNG....", content_type="image/png")
        self.attachment = CommentService.add(todo, CommentCreateSchema(body="", files=[upload])).attachments.get()

    def test_the_owner_can_download(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("comments:attachment", args=[self.attachment.pk])).status_code, 200)

    def test_anyone_else_is_refused(self):
        self.client.force_login(IdentityService.create_user("b@x.com"))
        self.assertEqual(self.client.get(reverse("comments:attachment", args=[self.attachment.pk])).status_code, 403)

    def test_search_finds_a_todo_by_attachment_name(self):
        self.assertEqual(CommentService.todo_ids_matching(self.owner, "shot"), {self.attachment.comment.todo_id})


class FilesizeTests(TestCase):
    def test_kilobytes_and_megabytes(self):
        self.assertEqual(filesize(500), "1 KB")
        self.assertEqual(filesize(1258291), "1.2 MB")
