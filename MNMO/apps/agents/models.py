from django.conf import settings
from django.db import models


class Agent(models.Model):
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    system_prompt = models.TextField()

    model_name = models.CharField(
        max_length=150,
        default="Qwen",
    )

    temperature = models.FloatField(default=0.2)
    top_k = models.PositiveSmallIntegerField(default=5)
    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_agents",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name