from decimal import Decimal

from django.conf import settings
from django.db.models import Count, DecimalField, F, Sum

from recordings.services import GroqService
from voice.models import VoiceCommand
from voice.services.decision_service import DecisionService


def money(amount: Decimal) -> str:
    """Dollars for a person to read: whole cents when that is exact ($0.25, $1.00), four places when it isn't
    ($0.0047, $0.9953), as a command costs a twentieth of a cent and rounding would hide it."""
    amount = Decimal(amount)
    cents = amount.quantize(Decimal("0.01"))
    return f"${cents}" if cents == amount else f"${amount.quantize(Decimal('0.0001'))}"


class VoiceService:
    @staticmethod
    def is_available(user) -> bool:
        """Voice mode needs both keys. Guests and members alike get it, each up to a budget."""
        return GroqService.is_configured() and DecisionService.is_configured()

    @staticmethod
    def limit(user) -> Decimal:
        return Decimal(settings.VOICE_GUEST_BUDGET_USD if user.is_guest else settings.VOICE_MEMBER_BUDGET_USD)

    @staticmethod
    def spent(user) -> Decimal:
        """Everything this user's voice commands have cost, in USD: the model and the speech together."""
        total = VoiceCommand.objects.filter(user=user).aggregate(
            total=Sum(F("llm_cost") + F("stt_cost"), output_field=DecimalField(max_digits=12, decimal_places=6)))["total"]
        return Decimal(total or 0)

    @staticmethod
    def over_budget(user) -> bool:
        return VoiceService.spent(user) >= VoiceService.limit(user)

    @staticmethod
    def limit_message(user) -> str:
        return f"You've used your {money(VoiceService.limit(user))} of voice."

    @staticmethod
    def usage(user) -> dict:
        """What Settings and the voice bar show: spent so far against the limit, as text and as a percentage."""
        spent, limit = VoiceService.spent(user), VoiceService.limit(user)
        commands = VoiceCommand.objects.filter(user=user).exclude(llm_cost=0, stt_cost=0).aggregate(n=Count("id"))["n"]
        percent = 100 if limit <= 0 else min(100, int(spent / limit * 100))
        return {"spent": money(spent), "limit": money(limit), "left": money(max(limit - spent, Decimal(0))),
                "percent": percent, "commands": commands, "full": spent >= limit}

    @staticmethod
    def speech_cost(seconds: float) -> Decimal:
        """Groq bills audio by the second at an hourly rate, with a minimum per request."""
        billed = max(seconds, settings.GROQ_MIN_BILLED_SECONDS) if seconds else 0
        return (Decimal(str(billed)) * Decimal(settings.GROQ_COST_PER_HOUR) / 3600).quantize(Decimal("0.000001"))
