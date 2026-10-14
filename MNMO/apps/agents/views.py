
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers

from .models import Agent
from .serializers import AgentSerializer
from .services.qwen_client import QwenClient, QwenAPIError


class AgentViewSet(viewsets.ModelViewSet):
    """
    API для создания, просмотра, изменения и удаления агентов.
    """

    queryset = Agent.objects.all().order_by("-created_at")
    serializer_class = AgentSerializer
    permission_classes = [AllowAny]


class AgentChatSerializer(serializers.Serializer):
    message = serializers.CharField(
        max_length=12000,
        allow_blank=False,
        trim_whitespace=True,
    )
    max_tokens = serializers.IntegerField(
        required=False,
        default=512,
        min_value=1,
        max_value=2048,
    )


class AgentChatView(APIView):
    """
    Отправка сообщения выбранному агенту.
    """

    permission_classes = [AllowAny]

    def post(self, request, agent_id):
        serializer = AgentChatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            agent = Agent.objects.get(
                pk=agent_id,
                is_active=True,
            )
        except Agent.DoesNotExist:
            return Response(
                {"error": "Активный агент не найден."},
                status=status.HTTP_404_NOT_FOUND,
            )

        message = serializer.validated_data["message"]
        max_tokens = serializer.validated_data["max_tokens"]

        system_prompt = (
            agent.system_prompt.strip()
            if agent.system_prompt
            else "Ты полезный ИИ-ассистент. Отвечай на русском языке."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]

        # Значение "Qwen" — стандартный маркер из модели Agent.
        # В таком случае используем модель из QWEN_MODEL в .env.
        configured_model = (
            agent.model_name
            if agent.model_name and agent.model_name != "Qwen"
            else None
        )

        try:
            client = QwenClient()
            answer = client.chat(
                messages=messages,
                temperature=agent.temperature,
                max_tokens=max_tokens,
                model=configured_model,
            )
        except QwenAPIError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "question": message,
                "answer": answer,
                "model": configured_model or client.model,
            },
            status=status.HTTP_200_OK,
        )
