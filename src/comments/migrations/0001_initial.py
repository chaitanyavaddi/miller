import django.db.models.deletion
from django.db import migrations, models

import comments.models


class Migration(migrations.Migration):
    """Adopts the existing todos_comment / todos_attachment tables. State only."""

    initial = True

    dependencies = [("todos", "0006_move_comments_to_comments_app")]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="Comment",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("body", models.TextField(blank=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("todo", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="comments", to="todos.todo")),
                    ],
                    options={"db_table": "todos_comment", "ordering": ["created_at", "id"]},
                ),
                migrations.CreateModel(
                    name="Attachment",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("file", models.FileField(max_length=500, upload_to=comments.models.attachment_upload_to)),
                        ("name", models.CharField(max_length=255)),
                        ("size", models.PositiveBigIntegerField(default=0)),
                        ("content_type", models.CharField(blank=True, max_length=100)),
                        ("comment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="comments.comment")),
                    ],
                    options={"db_table": "todos_attachment", "ordering": ["id"]},
                ),
            ],
        ),
    ]
