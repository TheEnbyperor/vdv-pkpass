# Generated by Django 5.0.9 on 2024-10-09 20:42

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0005_appledevice_alter_ticket_ticket_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="last_updated",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.CreateModel(
            name="AppleRegistration",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "device",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="registrations",
                        to="main.appledevice",
                    ),
                ),
                (
                    "ticket",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="apple_registrations",
                        to="main.ticket",
                    ),
                ),
            ],
            options={
                "unique_together": {("ticket", "device")},
            },
        ),
    ]
