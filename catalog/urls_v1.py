"""API v1 URL configuration — mirrors the main catalog/urls.py router."""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from catalog.views import TrackViewSet, GenreViewSet, ArtistViewSet, SurveyAPIView, AnalyticsAPIView, FeedbackAPIView, RecommendationMetricsView

router = DefaultRouter()
router.register(r'genres', GenreViewSet)
router.register(r'artists', ArtistViewSet)
router.register(r'tracks', TrackViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('feedback/', FeedbackAPIView.as_view(), name='feedback-v1'),
    path('survey/', SurveyAPIView.as_view(), name='survey-v1'),
    path('analytics/', AnalyticsAPIView.as_view(), name='analytics-v1'),
    path('metrics/', RecommendationMetricsView.as_view(), name='metrics-v1'),
]
