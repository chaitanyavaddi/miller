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
# Whisper's own measure of how sure it is of the words it wrote (an average log-probability). Below this it is
# mostly guessing, which is what it does with room noise: those segments are left out too.
CONFIDENCE_MIN = -1.0
USER_AGENT = "Miller/1.0 (+https://miller.so)"
# What a browser records in: Chrome and Firefox make webm or ogg, Safari (iPhones) makes mp4.
AUDIO_TYPES = {".webm": "audio/webm", ".mp4": "audio/mp4", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".wav": "audio/wav"}


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
    def transcribe(path: Path, prompt: str = "") -> list[dict]:
        """[{"start": s, "end": s, "text": ...}], seconds into the file. `prompt` is words to expect (names,
        mostly): Whisper spells them right instead of guessing."""
        return GroqService.transcribe_timed(path, prompt)[0]

    @staticmethod
    def transcribe_timed(path: Path, prompt: str = "") -> tuple[list[dict], float]:
        """The segments, and how many seconds of audio the file held (what Groq bills by)."""
        boundary = uuid.uuid4().hex
        fields = {"model": settings.GROQ_MODEL, "response_format": "verbose_json"}
        if prompt:
            fields["prompt"] = prompt
        body = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            for name, value in fields.items()
        )
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
                 f"Content-Type: {AUDIO_TYPES.get(path.suffix, 'audio/webm')}\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
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
        segments = [
            {"start": float(s.get("start", 0)), "end": float(s.get("end", 0)), "text": (s.get("text") or "").strip()}
            for s in result.get("segments", [])
            if (s.get("text") or "").strip() and float(s.get("no_speech_prob") or 0) < NO_SPEECH_MAX
            and float(s.get("avg_logprob") or 0) > CONFIDENCE_MIN
        ]
        return segments, float(result.get("duration") or 0)
