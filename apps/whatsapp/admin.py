from django.contrib import admin
from django.utils.html import format_html

from apps.whatsapp.models import WhatsAppMessage


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    """Admin interface for WhatsApp message history."""

    list_display = [
        "id",
        "direction",
        "message_type",
        "phone_number",
        "content_preview",
        "transcribed_text_preview",
        "has_media",
        "timestamp",
        "user_profile",
        "company",
        "api_success",
    ]
    list_filter = ["direction", "message_type", "timestamp", "company", "api_success"]
    search_fields = ["phone_number", "content", "transcribed_text", "whatsapp_message_id", "button_id"]
    date_hierarchy = "timestamp"
    readonly_fields = [
        "direction",
        "message_type",
        "phone_number",
        "timestamp",
        "content",
        "button_id",
        "whatsapp_message_id",
        "reply_to_message_id",
        "media_id",
        "media_url",
        "r2_media_link",
        "transcribed_text",
        "raw_payload",
        "api_success",
        "api_error",
        "user_profile",
        "company",
        "sale",
        "waitlist_entry",
    ]
    ordering = ["-timestamp"]

    def content_preview(self, obj):
        """Show truncated content in list view."""
        if obj.content:
            return obj.content[:100] + "..." if len(obj.content) > 100 else obj.content
        return f"[{obj.message_type}]"
    content_preview.short_description = "Content"

    def transcribed_text_preview(self, obj):
        """Show truncated transcription in list view."""
        if obj.transcribed_text:
            return obj.transcribed_text[:100] + "..." if len(obj.transcribed_text) > 100 else obj.transcribed_text
        return "-"
    transcribed_text_preview.short_description = "Transcription"

    def has_media(self, obj):
        """Show if message has media attached."""
        return bool(obj.r2_media_url or obj.media_id)
    has_media.boolean = True
    has_media.short_description = "Media"

    def r2_media_link(self, obj):
        """Show R2 media URL as a clickable link."""
        if obj.r2_media_url:
            return format_html('<a href="{}" target="_blank">View Media</a>', obj.r2_media_url)
        return "-"
    r2_media_link.short_description = "R2 Media URL"

    def has_add_permission(self, request):
        """Disable manual creation - messages are auto-recorded."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Allow deletion for cleanup."""
        return True
