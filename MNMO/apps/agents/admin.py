
from django.contrib import admin
from .models import Agent


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "model_name", "is_active", "created_at")
    list_filter = ("is_active", "model_name")
    search_fields = ("name", "description")
    ordering = ("-created_at",)
