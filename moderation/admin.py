from django.contrib import admin

from .models import Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = [
        "reporter",
        "target_repr",
        "reason",
        "is_resolved",
        "hides_target",
        "created_at",
    ]
    list_filter = ["reason", "is_resolved", "hides_target", "created_at"]
    search_fields = ["reporter__username", "detail"]
    actions = ["hide_reported_content", "dismiss_reports"]

    @admin.display(description="Target")
    def target_repr(self, obj):
        return str(obj.target) if obj.target else "(deleted)"

    @admin.action(description="Hide reported content")
    def hide_reported_content(self, request, queryset):
        queryset.update(hides_target=True, is_resolved=True)

    @admin.action(description="Dismiss reports")
    def dismiss_reports(self, request, queryset):
        queryset.update(is_resolved=True)
