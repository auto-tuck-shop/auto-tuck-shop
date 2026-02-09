import asyncio

from django.contrib import admin, messages
from django.db import close_old_connections, transaction
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
    readonly_fields = ["created_at", "approved_at", "approved_by", "company", "user_profile", "status"]
    actions = ["approve_entries", "reject_entries"]
    change_form_template = "admin/core/waitlistentry/change_form.html"

    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.readonly_fields)
        if not request.user.is_superuser:
            readonly += ["phone_number", "company_name", "first_message"]
        return readonly

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id)
        extra_context["show_approve_reject"] = (
            obj is not None and obj.status == WaitlistEntry.Status.PENDING
        )
        return super().change_view(request, object_id, form_url, extra_context)

    def response_change(self, request, obj):
        if "_approve" in request.POST and obj.status == WaitlistEntry.Status.PENDING:
            obj.status = WaitlistEntry.Status.APPROVED
            self._approve_entry(request, obj)
            return self.response_post_save_change(request, obj)
        if "_reject" in request.POST and obj.status == WaitlistEntry.Status.PENDING:
            obj.status = WaitlistEntry.Status.REJECTED
            obj.save()
            self.message_user(request, f"Rejected {obj.phone_number}.", messages.SUCCESS)
            return self.response_post_save_change(request, obj)
        return super().response_change(request, obj)

    def has_add_permission(self, request):
        if not request.user.is_superuser:
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_superuser:
            return False
        return super().has_delete_permission(request, obj)

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

        # Send approval notification via WhatsApp after the transaction commits
        phone = entry.phone_number

        def send_welcome():
            client = get_whatsapp_client()
            close_old_connections()
            asyncio.run(client.send_message(
                phone,
                f"Welcome to Auto Tuck Shop! Your account has been approved.\n\n"
                f"Company: {company_name}\n\n"
                f"You can now send sales messages to track your sales. For example:\n"
                f"'sold 2 cokes R15 each, 1 chips R10'\n"
                f"'3 waters R12 each, 2 chocolates R8 each'\n\n"
                f"As an owner, you can also add assistants by sending messages like:\n"
                f"'add assistant +27821234567'"
            ))

        transaction.on_commit(send_welcome)
        self.message_user(request, f"Approved {entry.phone_number}.", messages.SUCCESS)

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
