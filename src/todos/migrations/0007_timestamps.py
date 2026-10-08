import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("todos", "0006_move_comments_to_comments_app")]

    operations = [
        migrations.AddField(
            model_name="todo", name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now), preserve_default=False,
        ),
        migrations.AddField(
            model_name="positionday", name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now), preserve_default=False,
        ),
        migrations.AddField(
            model_name="positionday", name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now), preserve_default=False,
        ),
    ]
