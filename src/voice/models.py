from django.conf import settings
from django.db import models


class VoiceCommand(models.Model):
    """One thing said in voice mode, and what came of it: kept so a command can be undone and tuned."""

    class Outcome(models.TextChoices):
        APPLIED = "applied", "Applied"
        ASKED   = "asked",   "Asked"      # Miller asked something back instead of acting
        NOTHING = "nothing", "Nothing"    # nothing heard, or nothing to do
        FAILED  = "failed",  "Failed"

    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="voice_commands")
    session    = models.CharField(max_length=40, blank=True, db_index=True)   # one run of voice mode: a conversation
    transcript = models.TextField(blank=True)
    operations = models.JSONField(default=list)   # what the model asked for, as it said it
    applied    = models.JSONField(default=list)   # what passed validation and was done
    undo       = models.JSONField(default=list)   # how to put it back, in the order to do it
    reply      = models.TextField(blank=True)     # the line shown to the user
    outcome    = models.CharField(max_length=10, choices=Outcome.choices, default=Outcome.NOTHING)
    model      = models.CharField(max_length=100, blank=True)
    heard_ms   = models.PositiveIntegerField(default=0)   # audio in → words
    decide_ms  = models.PositiveIntegerField(default=0)   # words → the model's answer
    total_ms   = models.PositiveIntegerField(default=0)   # audio in → list changed
    audio_seconds = models.FloatField(default=0)
    tokens_in  = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    llm_cost   = models.DecimalField(max_digits=10, decimal_places=6, default=0)   # USD, as OpenRouter reports it
    stt_cost   = models.DecimalField(max_digits=10, decimal_places=6, default=0)   # USD, Groq's rate for the audio
    undone_at  = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.transcript[:60]

    @property
    def cost(self):
        return self.llm_cost + self.stt_cost

    def digest(self) -> str:
        """What this command came to, for the next one to read: operations with their ids, or the question asked."""
        if self.outcome == self.Outcome.APPLIED:
            def step(o):
                text = o["op"] + (f' #{o["todo_id"]}' if o.get("todo_id") else "") + (f' note {o["comment_id"]}' if o.get("comment_id") else "")
                text += f' "{o["text"]}"' if o.get("text") else ""
                if o["op"] == "move":
                    text += f' to {o["place"]}' + (f' #{o["anchor_id"]}' if o.get("anchor_id") else f' {o["position"]}' if o.get("position") else "")
                elif o["op"] == "open":
                    text += f' {o["target"]}'
                return text
            return "; ".join(step(o) for o in self.applied) + (" (then undone)" if self.undone_at else "")
        if self.outcome == self.Outcome.ASKED:
            return f'asked: "{self.reply}"'
        return "nothing done"
