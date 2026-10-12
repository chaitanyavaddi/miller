import json
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from django.conf import settings

TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# Whisper sometimes "hears" words in silence (a stray "Thank you."); a segment it rates as probably
# not speech is left out.
NO_SPEECH_MAX = 0.6
USER_AGENT = "Miller/1.0 (+https://miller.so)"


class TranscriptionError(Exception):
    """Groq refused or couldn't be reached. `retry_later` is set for its rate limit: try on a later run."""

    def __init__(self, message: str, retry_later: bool = False):
        super().__init__(message)
        self.retry_later = retry_later


class GroqService:
    """Groq's Whisper: one audio file in, its words out with segment timestamps."""

    @staticmethod
    def is_configured() -> bool:
        return bool(settings.GROQ_API_KEY)

    @staticmethod
    def transcribe(path: Path) -> list[dict]:
        """[{"start": s, "end": s, "text": ...}], seconds into the file."""
        boundary = uuid.uuid4().hex
        fields = {"model": settings.GROQ_MODEL, "response_format": "verbose_json"}
        body = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            for name, value in fields.items()
        )
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
                 f"Content-Type: audio/webm\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(TRANSCRIBE_URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type":  f"multipart/form-data; boundary={boundary}",
            "User-Agent":    USER_AGENT,  # Groq sits behind Cloudflare, which refuses Python's default agent (error 1010)
        })
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise TranscriptionError(error.read().decode(errors="replace")[:500], retry_later=error.code in (429, 500, 502, 503)) from error
        except (OSError, ValueError) as error:
            raise TranscriptionError(str(error), retry_later=True) from error
        return [
            {"start": float(s.get("start", 0)), "end": float(s.get("end", 0)), "text": (s.get("text") or "").strip()}
            for s in result.get("segments", [])
            if (s.get("text") or "").strip() and float(s.get("no_speech_prob") or 0) < NO_SPEECH_MAX
        ]
