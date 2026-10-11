from dataclasses import dataclass

from django.contrib import messages
from django.db import models

TITLE_MAX_LENGTH = 300


@dataclass
class TodoTitleSchema:
    title: str

    @classmethod
    def from_post(cls, request) -> "TodoTitleSchema":
        """Used both to add a todo and to rename one."""
        obj = cls(title=request.POST.get("title", "").strip())
        obj._request = request
        return obj

    def is_valid_title(self) -> bool:
        if not self.title:
            messages.error(self._request, "Write something first")
            return False
        if len(self.title) > TITLE_MAX_LENGTH:
            messages.error(self._request, f"Keep it under {TITLE_MAX_LENGTH} characters")
            return False
        return True

    def is_valid(self) -> bool:
        return all([self.is_valid_title()])


@dataclass
class TodoReorderSchema:
    ids: list[int]

    @classmethod
    def from_post(cls, request) -> "TodoReorderSchema":
        raw = request.POST.get("ids", "").split(",")
        obj = cls(ids=[int(i) for i in raw if i.strip().isdigit()])
        obj._request = request
        return obj

    def is_valid_ids(self) -> bool:
        if not self.ids:
            messages.error(self._request, "Nothing to reorder")
            return False
        return True

    def is_valid(self) -> bool:
        return all([self.is_valid_ids()])


@dataclass
class TodoReopenSchema:
    index: int
    select: bool

    @classmethod
    def from_post(cls, request) -> "TodoReopenSchema":
        raw = request.POST.get("index", "").strip()
        obj = cls(index=int(raw) if raw.isdigit() else 1, select=request.POST.get("select") == "1")
        obj._request = request
        return obj

    def is_valid(self) -> bool:
        return True
