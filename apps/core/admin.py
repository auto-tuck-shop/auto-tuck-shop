import asyncio

from django.contrib import admin, messages
from django.db import close_old_connections
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import Company, UserProfile, WaitlistEntry


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "active", "member_count", "created_at"]
    list_filter = ["active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}

    def member_count(self, obj):
        return obj.members.count()

    member_count.short_description = "Members"


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "Profile"


class UserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]
    list_display = [
        "username",
        "email",
        "first_name",
        "last_name",
        "get_company",
        "get_role",
        "is_staff",
    ]
    list_select_related = ["profile", "profile__company"]

    def get_company(self, obj):
        if hasattr(obj, "profile"):
            return obj.profile.company.name
        return "-"

    get_company.short_description = "Company"

    def get_role(self, obj):
        if hasattr(obj, "profile"):
            return obj.profile.get_role_display()
        return "-"

    get_role.short_description = "Role"


# Re-register UserAdmin
admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "company", "role", "phone_number"]
    list_filter = ["company", "role"]
    search_fields = ["user__username", "user__email", "phone_number"]
    autocomplete_fields = ["user", "company"]


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(admin.ModelAdmin):
    list_display = ["phone_number", "company_name", "status", "created_at", "approved_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["phone_number", "company_name", "notes"]
    readonly_fields = ["created_at", "approved_at", "approved_by", "company", "user_profile"]
    actions = ["approve_entries", "reject_entries"]

    fieldsets = (
        (None, {
            "fields": ("phone_number", "company_name", "status")
        }),
        ("Message", {
            "fields": ("first_message",),
            "classes": ("collapse",),
        }),
        ("Approval Details", {
            "fields": ("created_at", "approved_at", "approved_by", "company", "user_profile"),
        }),
        ("Notes", {
            "fields": ("notes",),
        }),
    )

    def save_model(self, request, obj, form, change):
        """Handle approval when status is changed to 'approved' via the detail page."""
        if change and "status" in form.changed_data:
            if obj.status == WaitlistEntry.Status.APPROVED and not obj.company:
                # Status changed to approved but no company yet - run approval logic
                self._approve_entry(request, obj)
                return  # _approve_entry calls save()
        super().save_model(request, obj, form, change)

    def _approve_entry(self, request, entry):
        """Run the full approval logic for a single entry."""
        from apps.whatsapp.services.whatsapp_client import get_whatsapp_client

        # Create company name from entry or generate fallback
        company_name = entry.company_name.strip() if entry.company_name else "Unnamed Shop"

        # Generate unique slug
        base_slug = slugify(company_name)
        slug = base_slug
        counter = 1
        while Company.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        # Create company
        company = Company.objects.create(name=company_name, slug=slug)

        # Create user (username from phone, removing non-alphanumeric)
        username = "".join(c for c in entry.phone_number if c.isalnum())
        user = User.objects.create_user(username=username)

        # Create user profile as owner
        profile = UserProfile.objects.create(
            user=user,
            company=company,
            role=UserProfile.Role.OWNER,
            phone_number=entry.phone_number,
        )

        # Update waitlist entry
        entry.approved_at = timezone.now()
        entry.approved_by = request.user
        entry.company = company
        entry.user_profile = profile
        entry.save()

        # Send approval notification via WhatsApp
        client = get_whatsapp_client()
        try:
            close_old_connections()
            asyncio.run(client.send_message(
                f"whatsapp:{entry.phone_number}",
                f"Welcome to Auto Tuck Shop! Your account has been approved.\n\n"
                f"Company: {company_name}\n\n"
                f"You can now send sales messages to track your sales. For example:\n"
                f"'sold 2 cokes R15 each, 1 chips R10'\n"
                f"'3 waters R12 each, 2 chocolates R8 each'\n\n"
                f"As an owner, you can also add assistants by sending messages like:\n"
                f"'add assistant +27821234567'"
            ))
            self.message_user(request, f"Approved {entry.phone_number} and sent WhatsApp notification.", messages.SUCCESS)
        except Exception as e:
            self.message_user(
                request,
                f"Approved {entry.phone_number} but failed to send WhatsApp notification: {e}",
                messages.WARNING
            )

    @admin.action(description="Approve selected waitlist entries")
    def approve_entries(self, request, queryset):
        approved_count = 0
        for entry in queryset.filter(status=WaitlistEntry.Status.PENDING):
            entry.status = WaitlistEntry.Status.APPROVED
            self._approve_entry(request, entry)
            approved_count += 1

        if approved_count > 0:
            self.message_user(
                request,
                f"Successfully approved {approved_count} waitlist entries.",
                messages.SUCCESS
            )
        else:
            self.message_user(
                request,
                "No pending entries to approve.",
                messages.WARNING
            )

    @admin.action(description="Reject selected waitlist entries")
    def reject_entries(self, request, queryset):
        updated = queryset.filter(status=WaitlistEntry.Status.PENDING).update(
            status=WaitlistEntry.Status.REJECTED
        )
        self.message_user(
            request,
            f"Rejected {updated} waitlist entries.",
            messages.SUCCESS
        )
