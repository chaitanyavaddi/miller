import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.services import IdentityService
from comments.schemas import CommentCreateSchema
from comments.service import CommentService
from comments.templatetags.comment_tags import filesize, linkify
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


class LinkifyTests(TestCase):
    def test_a_link_in_an_update_opens_in_a_new_tab_without_its_scheme(self):
        html = linkify("Spec at https://docs.example.com/plan/ please.")
        self.assertIn('<a class="comment__link" href="https://docs.example.com/plan/" target="_blank" rel="noopener noreferrer">docs.example.com/plan', html)
        self.assertTrue(html.endswith("</a> please."))  # the full stop stays the sentence's

    def test_www_addresses_get_https_and_everything_else_stays_escaped(self):
        html = linkify("<b>see</b> www.example.com")
        self.assertIn("&lt;b&gt;see&lt;/b&gt;", html)
        self.assertIn('href="https://www.example.com"', html)

    def test_a_long_address_is_shortened_on_screen_but_not_in_the_link(self):
        url = "https://example.com/" + "a" * 80
        html = linkify(url)
        self.assertIn(f'href="{url}"', html)
        self.assertIn("…", html)
