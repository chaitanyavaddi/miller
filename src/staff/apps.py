from django.contrib.admin.apps import AdminConfig


class MillerAdminConfig(AdminConfig):
    """Django's admin, as Miller's own site (staff.sites): Google sign-in only, read-only, with a usage report."""
    default_site = "staff.sites.MillerAdminSite"
