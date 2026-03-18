"""Add PrecomputedRecommendation model for materialized recommendation candidates."""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0012_add_denormalized_artist_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='PrecomputedRecommendation',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'distance',
                    models.FloatField(
                        help_text='Euclidean distance between source and recommended track feature vectors',
                    ),
                ),
                (
                    'computed_at',
                    models.DateTimeField(
                        auto_now=True,
                        help_text='When this recommendation was last computed',
                    ),
                ),
                (
                    'recommended_track',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='precomputed_as_rec',
                        to='catalog.track',
                    ),
                ),
                (
                    'source_track',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='precomputed_recs',
                        to='catalog.track',
                    ),
                ),
            ],
            options={
                'ordering': ['distance'],
                'unique_together': {('source_track', 'recommended_track')},
            },
        ),
        migrations.AddIndex(
            model_name='precomputedrecommendation',
            index=models.Index(
                fields=['source_track'],
                name='catalog_pre_source__idx',
            ),
        ),
    ]
