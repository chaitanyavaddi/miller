"""ASGI entry point: the web runs async (uvicorn workers under gunicorn), so a page can hold an open
event stream (/events/) for live updates. Ordinary views stay sync; Django runs them in threads."""
import os
import sys
from pathlib import Path

from django.core.asgi import get_asgi_application

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
application = get_asgi_application()
