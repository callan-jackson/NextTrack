"""Management command to export analytics data."""

import csv
import json
import sys
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Count, Avg

from catalog.models import AnalyticsEvent, RecommendationFeedback, UserSurvey


class Command(BaseCommand):
    help = 'Export analytics report for a given period'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30, help='Number of days to report on')
        parser.add_argument('--format', type=str, default='csv', choices=['csv', 'json'], help='Output format')

    def handle(self, *args, **options):
        days = options['days']
        fmt = options['format']
        cutoff = timezone.now() - timedelta(days=days)

        events = AnalyticsEvent.objects.filter(created_at__gte=cutoff)
        feedback = RecommendationFeedback.objects.filter(created_at__gte=cutoff)
        surveys = UserSurvey.objects.filter(created_at__gte=cutoff)

        daily_sessions = events.values('created_at__date').annotate(
            sessions=Count('session_key', distinct=True)
        ).order_by('created_at__date')

        likes = feedback.filter(score=True).count()
        dislikes = feedback.filter(score=False).count()

        survey_avgs = surveys.aggregate(
            avg_satisfaction=Avg('overall_satisfaction'),
            avg_discovery=Avg('discovery_rating'),
            avg_accuracy=Avg('accuracy_rating'),
        )

        report = {
            'period_days': days,
            'total_events': events.count(),
            'total_feedback': feedback.count(),
            'likes': likes,
            'dislikes': dislikes,
            'total_surveys': surveys.count(),
            'avg_satisfaction': round(survey_avgs['avg_satisfaction'] or 0, 2),
            'avg_discovery': round(survey_avgs['avg_discovery'] or 0, 2),
            'avg_accuracy': round(survey_avgs['avg_accuracy'] or 0, 2),
            'daily_sessions': [
                {'date': str(d['created_at__date']), 'sessions': d['sessions']}
                for d in daily_sessions
            ],
        }

        if fmt == 'json':
            self.stdout.write(json.dumps(report, indent=2))
        else:
            writer = csv.writer(sys.stdout)
            writer.writerow(['Metric', 'Value'])
            writer.writerow(['Period (days)', report['period_days']])
            writer.writerow(['Total Events', report['total_events']])
            writer.writerow(['Total Feedback', report['total_feedback']])
            writer.writerow(['Likes', report['likes']])
            writer.writerow(['Dislikes', report['dislikes']])
            writer.writerow(['Total Surveys', report['total_surveys']])
            writer.writerow(['Avg Satisfaction', report['avg_satisfaction']])
            writer.writerow(['Avg Discovery', report['avg_discovery']])
            writer.writerow(['Avg Accuracy', report['avg_accuracy']])
            writer.writerow([])
            writer.writerow(['Date', 'Active Sessions'])
            for d in report['daily_sessions']:
                writer.writerow([d['date'], d['sessions']])
