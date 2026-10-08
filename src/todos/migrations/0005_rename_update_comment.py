import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Update -> Comment, data preserved (renames, not drop-and-create)."""

    dependencies = [("todos", "0004_todo_deleted_at")]

    operations = [
        migrations.RenameModel(old_name="Update", new_name="Comment"),
        migrations.AlterField(
            model_name="comment", name="todo",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="comments", to="todos.todo"),
        ),
        migrations.RenameField(model_name="attachment", old_name="update", new_name="comment"),
    ]
