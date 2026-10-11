import django.db.models.deletion
from django.db import migrations, models


def forget_recall_connections(apps, schema_editor):
    """Recall held these connections' refresh tokens, so they can't be carried over: their meetings
    come off the list and each account is connected again."""
    Todo = apps.get_model("todos", "Todo")
    CalendarConnection = apps.get_model("calendars", "CalendarConnection")
    Todo.objects.filter(calendar_events__isnull=False).delete()
    CalendarConnection.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("calendars", "0001_initial"),
        ("todos", "0008_alter_todo_options_and_more"),
    ]

    operations = [
        migrations.RunPython(forget_recall_connections, migrations.RunPython.noop),
        migrations.RemoveField(model_name="calendarevent", name="connection"),
        migrations.RemoveField(model_name="calendarevent", name="recall_id"),
        migrations.RemoveField(model_name="calendarconnection", name="recall_id"),
        migrations.AddField(
            model_name="calendarconnection",
            name="refresh_token",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="calendarconnection",
            name="status",
            field=models.CharField(
                choices=[("connected", "Connected"), ("disconnected", "Disconnected")],
                default="connected",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="CalendarSource",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("external_id", models.CharField(max_length=1024)),
                ("name", models.CharField(max_length=255)),
                ("is_primary", models.BooleanField(default=False)),
                ("enabled", models.BooleanField(default=False)),
                ("channel_id", models.CharField(blank=True, db_index=True, max_length=64)),
                ("channel_resource", models.CharField(blank=True, db_index=True, max_length=255)),
                ("channel_expires_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sources",
                        to="calendars.calendarconnection",
                    ),
                ),
            ],
            options={
                "ordering": ["-is_primary", "name"],
                "unique_together": {("connection", "external_id")},
            },
        ),
        migrations.AddField(
            model_name="calendarevent",
            name="source",
            field=models.ForeignKey(
                default=None,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="events",
                to="calendars.calendarsource",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="calendarevent",
            name="external_id",
            field=models.CharField(default="", max_length=1024),
            preserve_default=False,
        ),
        migrations.AlterUniqueTogether(name="calendarevent", unique_together={("source", "external_id")}),
    ]
