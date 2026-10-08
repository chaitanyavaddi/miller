from dataclasses import dataclass

from django.utils import timezone

from comments.service import CommentService
from todos.schemas import ReviewFilterSchema, ReviewPeriod, ReviewStatus
from todos.services import Insights, InsightsService, TodoService

CHART_WIDTH  = 640
CHART_HEIGHT = 72   # plot area; day labels sit below it
LABEL_SPACE  = 18
BAR_WIDTH    = 4
BAR_RADIUS   = 2


@dataclass(frozen=True)
class ReviewRow:
    id: int
    title: str
    status: str
    stamp: str
    days_on_list: int

    @property
    def can_reopen(self) -> bool:
        return self.status != ReviewStatus.OPEN

    @property
    def state_label(self) -> str:
        return {ReviewStatus.DONE: "Closed", ReviewStatus.DELETED: "Deleted"}.get(self.status, "Open")


@dataclass(frozen=True)
class ChartBar:
    path: str
    slot_x: float
    slot_width: float
    label_x: float
    label: str
    show_label: bool
    value_label: str
    value_y: float
    tooltip: str
    is_current: bool
    is_peak: bool


@dataclass(frozen=True)
class ReviewChart:
    width: int
    height: int
    baseline: int
    label_y: int
    bars: list[ChartBar]


class ReviewService:
    """Read model for the review modal: every todo by state and period, what that period adds up
    to, and an ink-thin chart of closes."""

    @staticmethod
    def build(user, filters: ReviewFilterSchema) -> dict:
        insights = InsightsService.for_period(user, filters.period)
        return {
            "filters":  filters,
            "statuses": ReviewStatus.choices,
            "periods":  ReviewPeriod.choices,
            "insights": insights,
            "chart":    ReviewService.chart(insights),
            "rows":     ReviewService.rows(user, filters),
        }

    @staticmethod
    def rows(user, filters: ReviewFilterSchema) -> list[ReviewRow]:
        since = InsightsService.window(filters.period).start
        return [ReviewService.row(todo) for todo in TodoService.reviewed(user, filters.status, since)]

    @staticmethod
    def row(todo) -> ReviewRow:
        ended = todo.deleted_at or todo.closed_at
        if todo.deleted_at:
            status = ReviewStatus.DELETED
        elif todo.closed_at:
            status = ReviewStatus.DONE
        else:
            status = ReviewStatus.OPEN
        return ReviewRow(
            id=todo.id,
            title=todo.title,
            status=status,
            stamp=ReviewService._day(timezone.localtime(ended).date()) if ended else f"#{todo.position}",
            days_on_list=InsightsService.days_between(todo.created_at, ended or timezone.now()),
        )

    @staticmethod
    def thread(todo, filters: ReviewFilterSchema) -> dict:
        return {"filters": filters, "row": ReviewService.row(todo), "comments": CommentService.for_todo(todo.id)}

    @staticmethod
    def chart(insights: Insights) -> ReviewChart:
        buckets = insights.buckets
        counts = [b.count or 0 for b in buckets]
        peak = max(counts, default=0)
        peak_index = counts.index(peak) if peak else None   # label one peak, not every tie
        slot = CHART_WIDTH / max(len(buckets), 1)
        bars = []
        for i, bucket in enumerate(buckets):
            count = counts[i]
            top = CHART_HEIGHT - (count / peak * (CHART_HEIGHT - 14) if peak else 0)
            bars.append(ChartBar(
                path=ReviewService._column(i * slot + (slot - BAR_WIDTH) / 2, top) if count else "",
                slot_x=round(i * slot, 1),
                slot_width=round(slot, 1),
                label_x=round(i * slot + slot / 2, 1),
                label=bucket.label,
                show_label=ReviewService._shows_label(i, len(buckets), bucket, insights.period),
                value_label=str(count),
                value_y=round(top - 6, 1),
                tooltip=ReviewService._tooltip(bucket, insights.period),
                is_current=bucket.is_current,
                is_peak=i == peak_index,
            ))
        return ReviewChart(width=CHART_WIDTH, height=CHART_HEIGHT + LABEL_SPACE, baseline=CHART_HEIGHT,
                           label_y=CHART_HEIGHT + LABEL_SPACE - 4, bars=bars)

    @staticmethod
    def _shows_label(index: int, total: int, bucket, period: str) -> bool:
        """Every day of a week; the ends and today for a month or the all-time weeks."""
        return period == ReviewPeriod.WEEK or index in (0, total - 1) or bucket.is_current

    @staticmethod
    def _tooltip(bucket, period: str) -> str:
        when = f"Week of {ReviewService._day(bucket.start)}" if period == ReviewPeriod.ALL else f"{bucket.start:%a} {ReviewService._day(bucket.start)}"
        return when if bucket.count is None else f"{when}: {bucket.count} closed"

    @staticmethod
    def _day(day) -> str:
        return f"{day:%b} {day.day}"

    @staticmethod
    def _column(x: float, top: float) -> str:
        """A thin column, round at its data end and square on the baseline."""
        r, right = BAR_RADIUS, x + BAR_WIDTH
        return f"M{x:.1f} {CHART_HEIGHT} V{top + r:.1f} A{r} {r} 0 0 1 {right:.1f} {top + r:.1f} V{CHART_HEIGHT} Z"
