
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AgentViewSet, AgentChatView

router = DefaultRouter()
router.register("agents", AgentViewSet, basename="agent")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "agents/<int:agent_id>/chat/",
        AgentChatView.as_view(),
        name="agent-chat",
    ),
]
