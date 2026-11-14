
from django.urls import path

from .views import (
    home,
    FeatureListCreateAPIView,
    FeatureDetailAPIView,
    evaluate_mission,
)

urlpatterns = [
    path('', home, name='home'),

    path(
        'api/features/',
        FeatureListCreateAPIView.as_view(),
        name='feature-list',
    ),

    path(
        'api/features/<int:pk>/',
        FeatureDetailAPIView.as_view(),
        name='feature-detail',
    ),

    path(
        'api/missions/evaluate/',
        evaluate_mission,
        name='evaluate-mission',
    ),
]
