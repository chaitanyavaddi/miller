from django.db import migrations


class Migration(migrations.Migration):
    """Comment and Attachment now belong to the `comments` app. State only: the tables stay."""

    dependencies = [("todos", "0005_rename_update_comment")]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="Attachment"),
                migrations.DeleteModel(name="Comment"),
            ],
        ),
    ]
