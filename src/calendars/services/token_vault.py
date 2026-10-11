from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class TokenVault:
    """Refresh tokens are encrypted at rest with CALENDAR_TOKEN_KEY (a Fernet key), so a leaked
    database alone can't read anyone's calendar."""

    @staticmethod
    def is_configured() -> bool:
        return bool(settings.CALENDAR_TOKEN_KEY)

    @staticmethod
    def seal(token: str) -> str:
        return Fernet(settings.CALENDAR_TOKEN_KEY).encrypt(token.encode()).decode()

    @staticmethod
    def open(sealed: str) -> str:
        """The token, or "" if it can't be read (the key changed): the connection then needs a reconnect."""
        try:
            return Fernet(settings.CALENDAR_TOKEN_KEY).decrypt(sealed.encode()).decode()
        except (InvalidToken, ValueError):
            return ""
