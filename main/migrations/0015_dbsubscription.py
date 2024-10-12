# Generated by Django 5.0.9 on 2024-10-12 21:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0014_ticket_account'),
    ]

    operations = [
        migrations.CreateModel(
            name='DBSubscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('device_token', models.CharField(max_length=255, unique=True, verbose_name='Device token')),
                ('refresh_at', models.DateTimeField(verbose_name='Refresh at')),
                ('info', models.JSONField(default=dict, verbose_name='Info')),
            ],
        ),
    ]