Ozone Coding Patterns
These are the exact coding conventions used in this project. Follow them strictly for every piece of code you write — no deviations, no "improvements", no alternative patterns.

1. VIEWS — Class-Based Views Only
All views inherit from django.views.View. Use LoginRequiredMixin for auth. Use OrgLoginRequiredMixin for org-scoped views (inside org subdomains).

Pattern:

from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from utils.view_helper import htmx, redirect_to

class ThingCreateView(OrgLoginRequiredMixin, View):
    def get(self, request):
        return render(request, "app/thing_create.html")

    def post(self, request):
        hx       = htmx(request)
        data     = ThingCreateSchema.from_post(request)
        template = 'app/thing_create.html#thing-form' if hx else 'app/thing_create.html'

        if not data.is_valid():
            return render(request, template, {'data': data})

        # business logic errors
        if SomeService.already_exists(request.org, data.name):
            messages.error(request, f'"{data.name}" already exists')
            return render(request, template, {'data': data})

        # persist
        Thing.objects.create(name=data.name, organization=request.org)
        return redirect_to(request, "things:list")

Rules:

NEVER use function-based views
NEVER use DRF ViewSets or APIView
NEVER use Django's FormView, CreateView, UpdateView, etc.
GET renders a form; POST validates → persists → redirects (or re-renders with errors)
Extract complex context into a _ctx(self, request, data) helper method when context has 3+ items
request.org (Organization) and request.domain are available on all org-subdomain views via middleware
2. SCHEMAS — Dataclasses, Not Django Forms
Use Python dataclasses with a from_post() classmethod and granular is_valid_*() methods. Never use Django forms, ModelForms, or DRF serializers.

Pattern:

from dataclasses import dataclass
from django.contrib import messages

@dataclass
class NestedInput:
    email: str
    role: str

@dataclass
class ThingCreateSchema:
    name: str
    color: str
    items: list[NestedInput]

    @classmethod
    def from_post(cls, request) -> "ThingCreateSchema":
        POST   = request.POST
        emails = POST.getlist('item_email')
        roles  = POST.getlist('item_role')
        obj = cls(
            name  = POST.get('name', '').strip(),
            color = POST.get('color', '#5E6AD2').strip(),
            items = [
                NestedInput(email=e.strip(), role=r.strip())
                for e, r in zip(emails, roles)
                if e.strip()
            ],
        )
        obj._request = request
        return obj

    def is_valid_name(self) -> bool:
        if not self.name:
            messages.error(self._request, 'Name is required')
            return False
        return True

    def is_valid_color(self) -> bool:
        if not self.color.startswith('#'):
            messages.error(self._request, 'Invalid color')
            return False
        return True

    def is_valid_items(self) -> bool:
        ok = True
        for item in self.items:
            if not re.match(r'^[^@]+@[^@]+\.[^@]+$', item.email):
                messages.error(self._request, f'"{item.email}" is not a valid email')
                ok = False
        return ok

    def is_valid(self) -> bool:
        results = [
            self.is_valid_name(),
            self.is_valid_color(),
            self.is_valid_items(),
        ]
        return all(results)  # Run ALL validators, not short-circuit

Rules:

Always store obj._request = request in from_post() so validators can call messages.error()
is_valid() must call ALL validators (not short-circuit with and) so all errors show at once
Use .strip() on all string fields from POST
Use getlist() for multi-value fields
Nested structures get their own dataclass
No raise ValidationError — errors go into Django messages only
3. MODELS — Flat with TextChoices
Use models.TextChoices for enums. Add created_at/updated_at on all models. Always define __str__, related_name, and unique_together where relevant.

Pattern:

from django.db import models
from django.conf import settings

class Thing(models.Model):
    class Status(models.TextChoices):
        ACTIVE   = 'active',   'Active'
        ARCHIVED = 'archived', 'Archived'

    organization = models.ForeignKey('organizations.Organization', on_delete=models.CASCADE, related_name='things')
    created_by   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='created_things')
    name         = models.CharField(max_length=200)
    status       = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

class ThingMember(models.Model):
    class Role(models.TextChoices):
        ADMIN  = 'admin',  'Admin'
        MEMBER = 'member', 'Member'

    thing = models.ForeignKey(Thing, on_delete=models.CASCADE, related_name='members')
    user  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='thing_memberships')

    class Meta:
        unique_together = ('thing', 'user')

    def __str__(self):
        return f"{self.user} → {self.thing}"

Rules:

NO custom managers — use Model.objects.filter(...) directly in views/services
NO soft deletes — use physical CASCADE deletion
NO abstract base models — repeat created_at/updated_at fields explicitly
related_name must be descriptive (not '+')
Enums always use TextChoices, never IntegerChoices or separate Python enums
null=True, blank=True only when field is genuinely optional
4. ERROR HANDLING — Django Messages Only
Errors are never returned as JSON or raised as exceptions to the view. All validation errors and business logic errors go into messages.error(request, '...') and the form partial is re-rendered.

Pattern:

# Schema-level
def is_valid_name(self) -> bool:
    if not self.name:
        messages.error(self._request, 'Name is required')
        return False
    return True

# View-level business logic
if ThingService.name_taken(request.org, data.name):
    messages.error(request, f'"{data.name}" is already taken')
    return render(request, template, {'data': data})

Error display in templates:

{% if messages %}
<ul class="form-messages">
  {% for m in messages %}
    <li class="form-message form-message--{{ m.tags }}">{{ m }}</li>
  {% endfor %}
</ul>
{% endif %}

CSS for messages (already in main.css):

.form-message--error → red background
.form-message--success → green background
Rules:

NEVER return HTTP 400/403/404 for form validation — always re-render with messages
NEVER use raise forms.ValidationError
NEVER put error text in the response JSON
HTTP 200 for error re-renders, HTTP 302 for successful redirects
Show ALL errors at once (don't stop at first failure)
5. HTMX — Form Partials with django-template-partials
Every form is wrapped in a {% partialdef name inline %} block. HTMX posts to the same URL, targets the form's own id, swaps outerHTML. On validation failure the view re-renders just the partial; on success it redirects.

Template pattern:

{% load partials %}

{% partialdef thing-form inline %}
<form id="thing-form"
      method="POST"
      hx-post="{% url 'things:create' %}"
      hx-target="#thing-form"
      hx-swap="outerHTML"
      hx-indicator="#thing-submit-btn">
  {% csrf_token %}

  {% if messages %}
  <ul class="form-messages">
    {% for m in messages %}
      <li class="form-message form-message--{{ m.tags }}">{{ m }}</li>
    {% endfor %}
  </ul>
  {% endif %}

  <div class="field">
    <input type="text" name="name" placeholder="Name" value="{{ data.name|default:'' }}">
  </div>

  <button type="submit" class="btn-primary" id="thing-submit-btn">
    <span class="btn-label">Create</span>
    <span class="btn-spinner"></span>
  </button>
</form>
{% endpartialdef %}

View selects partial vs full page:

TEMPLATE = 'app/thing_create.html'

def post(self, request):
    hx       = htmx(request)
    data     = ThingCreateSchema.from_post(request)
    template = f'{TEMPLATE}#thing-form' if hx else TEMPLATE
    ...

HTMX attributes used in this project:

Attribute	Purpose
hx-post="url"	POST form via HTMX
hx-get="url"	Load modal/partial via GET
hx-target="#id"	Element to replace
hx-swap="outerHTML"	Replace whole element (default for forms)
hx-swap="innerHTML"	Replace contents (for slots like #modal-slot)
hx-indicator="#btn"	Show spinner on this element during request
Modal load pattern:

<!-- Trigger button -->
<button hx-get="{% url 'things:modal' %}"
        hx-target="#modal-slot"
        hx-swap="innerHTML">
  New Thing
</button>

<!-- Slot at bottom of page -->
<div id="modal-slot"></div>

Modal view:

class ThingModalView(OrgLoginRequiredMixin, View):
    def get(self, request):
        return render(request, "app/partials/new_thing.html", {...})

Rules:

NEVER use hx-boost
NEVER use hx-push-url (URL changes happen via server redirects)
NEVER return JSON from views — always HTML fragments
The partial id must match the hx-target selector
Always include {% csrf_token %} inside HTMX forms
Input value="{{ data.field|default:'' }}" to preserve values on re-render
6. HTMX UTILITIES — utils/view_helper.py
Always import and use these two helpers:

from utils.view_helper import htmx, redirect_to

htmx(request) — returns an HtmxInfo object, truthy if it's an HTMX request:

hx = htmx(request)
if hx:
    template = f'{TEMPLATE}#partial-id'

redirect_to(request, url_name) — handles redirect for both HTMX and regular requests:

# HTMX request: returns HttpResponse with HX-Redirect header
# Regular request: returns standard Django redirect()
return redirect_to(request, "things:list")

NEVER do return HttpResponseRedirect(...) or return redirect(...) directly after a successful POST — always use redirect_to().

7. SERVICE LAYER — Static Methods, No Logic in Views
Complex business logic and multi-model queries go into a service.py file per app.

Pattern:

# src/things/service.py
from src.things.models import Thing

class ThingService:
    @staticmethod
    def get_all_things(org):
        return Thing.objects.filter(organization=org).order_by('-created_at')

    @staticmethod
    def name_taken(org, name: str) -> bool:
        return Thing.objects.filter(organization=org, name__iexact=name).exists()

    @staticmethod
    def create_thing(org, user, data):
        thing = Thing.objects.create(
            organization=org,
            created_by=user,
            name=data.name,
            color=data.color,
        )
        for item in data.items:
            ThingItem.objects.create(thing=thing, email=item.email, role=item.role)
        return thing

Rules:

Only @staticmethod methods — no self, no instance state
Services handle multi-step persistence (create thing + create related records)
Views stay thin: parse → validate → call service → redirect
No ORM queries directly in templates
Fat service.py / views.py → a folder. Once a single service.py or views.py is bundling more than one real concern (different reasons to change, not just "many methods"), split it into a package — one file per concern, plus an __init__.py that re-exports everything so every other import in the codebase (from app.service import ThingService, from . import views) keeps working unchanged:

src/things/service.py            →   src/things/services/__init__.py         (re-exports)
                                      src/things/services/thing_service.py    (ThingService)
                                      src/things/services/secrets_service.py  (SecretsService)

src/things/views.py              →   src/things/views/__init__.py            (re-exports)
                                      src/things/views/thing_views.py         (ThingListView, ThingCreateView, ...)
                                      src/things/views/run_views.py           (RunListView, RunDetailView, ...)

# src/things/services/__init__.py
from things.services.thing_service import ThingService
from things.services.secrets_service import SecretsService

__all__ = ['ThingService', 'SecretsService']

Name each split file explicitly after what it holds — thing_service.py, secrets_service.py, thing_views.py — never a bare noun like things.py that could be a model, a service, or a view file depending on which folder you're standing in.

Split along the same seams the classes already imply — don't invent new ones. If it's genuinely one concern with many methods, a big file is fine; don't split just to hit a line count.

The __init__.py re-export means urls.py's from . import views and any from app.service import X elsewhere never need to change — only the file that was split, and its own internal imports.

This is a project-wide convention, not a per-app opt-in — apply it to any app's service.py/views.py once it earns it.

8. URL PATTERNS
# src/things/urls.py
from django.urls import path
from src.things.views import ThingListView, ThingCreateView, ThingModalView

app_name = 'things'

urlpatterns = [
    path('',        ThingListView.as_view(),  name='list'),
    path('new/',    ThingCreateView.as_view(), name='create'),
    path('modal/',  ThingModalView.as_view(),  name='modal'),
]

Rules:

app_name required on every URLconf
Use name='list', name='create', name='modal', name='edit', name='delete' — standard names
Paths end with /
Include in config/urls.py or config/urls_org.py depending on whether it's a main-domain or org-subdomain route
9. TEMPLATES
Template inheritance:

{% extends "base.html" %}
{% load static %}
{% load partials %}

{% block title %}Things - Ozone{% endblock %}

{% block extra_css %}
<link rel="stylesheet" href="{% static 'css/things.css' %}">
{% endblock %}

{% block content %}
...
{% endblock %}

Rules:

Templates are presentation-only — no business logic, no ORM calls, no arithmetic
Use {{ var|default:'' }} to prevent "None" showing in inputs
Use {% url 'app:name' %} for all URLs, never hardcode paths
CSS goes in static/css/, loaded via {% block extra_css %}
Inline <script> tags are fine for modal/form JS at the bottom of a partial
Inline scripts MUST be wrapped in an IIFE: (function() { ... })()
10. JAVASCRIPT CONVENTIONS
Vanilla JS only — no Alpine.js, no React, no Vue. HTMX handles server communication. JS handles purely client-side UI (modal open/close, color pickers, dropdowns).

Pattern:

<script>
(function () {
  const modal = document.getElementById('modal-new-thing');

  // Guard against re-init on HTMX swaps
  if (!modal._init) {
    modal._init = true;
    document.getElementById('btn-open-modal').addEventListener('click', () => modal.classList.add('open'));
  }

  // Re-bind on every swap (these are inside the swap target)
  document.getElementById('cancel-btn').addEventListener('click', () => modal.classList.remove('open'));

  // Event delegation for dynamic rows
  modal.addEventListener('click', e => {
    const btn = e.target.closest('.remove-row-btn');
    if (!btn) return;
    btn.closest('.row').remove();
  });
})();
</script>

Rules:

Always wrap in IIFE (function() { ... })()
Use element.classList.add/remove/toggle() for state changes
Use e.target.closest('.selector') for event delegation on dynamic elements
Guard persistent listeners with if (!element._init) { element._init = true; ... }
Re-bind transient listeners (inside HTMX swap targets) unconditionally
Use data-* attributes to store IDs/values on DOM elements
DOM creation for dynamic rows: document.createElement() + .innerHTML
11. CSS CONVENTIONS
Custom semantic CSS — NOT Tailwind. Design tokens are CSS variables in :root.

Key variables (defined in static/css/main.css):

--bg, --surface, --surface-hover, --surface-active
--border, --border-strong
--text, --text-2, --text-3, --text-4
--accent, --accent-hover, --accent-bg, --accent-text
--font-ui, --font-mono

Status colors: --st-open, --st-progress, --st-blocked, --st-review, --st-done (each has a -bg variant)

Button spinner (HTMX loading state):

<button type="submit" class="btn-primary" id="my-btn">
  <span class="btn-label">Submit</span>
  <span class="btn-spinner"></span>
</button>

The .btn-spinner auto-shows when .htmx-request class is on the button (via hx-indicator="#my-btn").

Rules:

Never use inline styles except for dynamic values (e.g., style="background: {{ color }}")
Class names are BEM-like: component, component__element, component--modifier
New feature CSS goes in a new file (static/css/things.css), not appended to main.css
No CSS frameworks, no utility classes
12. PERMISSIONS — django-rules Predicates + Mixins
Object-level permissions are plain Python predicate functions in <app>/rules.py, registered against app_label.action strings, and checked via Django's own has_perm(perm, obj). Never hand-roll role checks (if request.thing_role == 'admin') once a permission exists for that capability — the predicate file is the single source of truth for who can do what.

Pattern — <app>/rules.py:

# src/things/rules.py
import rules
from things.models import ThingMember

@rules.predicate
def is_thing_member(user, thing):
    if thing is None:
        return False
    return ThingMember.objects.filter(thing=thing, user=user).exists()

@rules.predicate
def is_thing_admin(user, thing):
    if thing is None:
        return False
    return ThingMember.objects.filter(thing=thing, user=user, role=ThingMember.Role.ADMIN).exists()

# One line per capability — the whole story of who can do what.
rules.add_perm('things.create_thing',   rules.is_superuser)
rules.add_perm('things.view_thing',     is_thing_member | rules.is_superuser)
rules.add_perm('things.manage_members', is_thing_admin  | rules.is_superuser)
rules.add_perm('things.delete_thing',   is_thing_admin  | rules.is_superuser)

Object-scoped views (URL has a slug/id identifying the object) — a small per-app mixin resolves the object once and checks the permission against it:

# src/things/mixins.py
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from things.models import Thing

class ThingPermissionRequiredMixin(LoginRequiredMixin):
    """Requires login + a `things.*` permission against the `thing_slug` URL kwarg."""
    login_url = '/login/'
    permission_required = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.thing = get_object_or_404(Thing, slug=kwargs.get('thing_slug'))
        request.thing = self.thing
        if self.permission_required and not request.user.has_perm(self.permission_required, self.thing):
            raise PermissionDenied(f'Missing permission: {self.permission_required}')
        return super().dispatch(request, *args, **kwargs)

class ThingSettingsView(ThingPermissionRequiredMixin, View):
    permission_required = 'things.manage_members'

Object-less views (create views, or any permission that doesn't need an instance) — use rules.contrib.views.PermissionRequiredMixin directly, not the per-app mixin:

from rules.contrib.views import PermissionRequiredMixin

class ThingCreateView(PermissionRequiredMixin, View):
    permission_required = 'things.create_thing'

For a plain View with no get_object, it checks the permission with obj=None automatically — exactly right when the predicate doesn't take an object (e.g. rules.is_superuser).

Checking permissions anywhere else (templates, ad hoc logic) — always request.user.has_perm('app.perm_name', obj), never re-derive the same logic by hand.

Rules:

One predicate function per condition, one rules.add_perm() line per capability — all in <app>/rules.py, nowhere else
Predicates are plain functions decorated with @rules.predicate — no ORM logic outside them
Compose predicates with &, |, ~ — never branch permission logic inside a view
config/settings/base.py — AUTHENTICATION_BACKENDS must list rules.permissions.ObjectPermissionBackend before ModelBackend, and INSTALLED_APPS uses 'rules.apps.AutodiscoverRulesConfig' (not plain 'rules') so every app's rules.py is autodiscovered
Object-scoped checks → a per-app <Thing>PermissionRequiredMixin; object-less checks (create views, global actions) → rules.contrib.views.PermissionRequiredMixin directly
NEVER write if not request.user.has_perm(...): raise PermissionDenied(...) inside a view body — that belongs in a mixin's permission_required
NEVER check roles via string comparison (if role == 'admin') once a permission exists for that capability — always has_perm
QUICK REFERENCE CHECKLIST
When creating a new feature, follow this order:

Model (src/app/models.py) — TextChoices, timestamps, str, related_name
Migration — python manage.py makemigrations && migrate
Schema (src/app/schemas.py) — dataclass + from_post() + is_valid_*() + is_valid()
Service (src/app/service.py) — static methods for queries and creation
Permissions (src/app/rules.py) — if the feature has role/object-based access, predicates + rules.add_perm() before writing views that need them
Views (src/app/views.py) — CBV, htmx() detection, schema validation, redirect_to(), gated by the right permission mixin
URLs (src/app/urls.py) — app_name, named paths
Register URLs — add include() to config/urls.py or config/urls_org.py
Template (templates/app/thing.html) — extends base, partialdef with HTMX form
CSS (static/css/app.css) — if needed, link in template's extra_css block