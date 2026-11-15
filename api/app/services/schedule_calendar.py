"""Working-window and interval helpers for earliest-fit scheduling."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from flask import g, has_app_context
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.constants.scheduling import SCHEDULE_HORIZON_DAYS, SHOP_TIMEZONE
from app.models.shop_settings import DEFAULT_BREAK_END, DEFAULT_BREAK_START, ShopSettings
from app.models.worker_skill import CalendarExceptionType, WorkCalendarException, WorkerSchedule

SHOP_TZ = ZoneInfo(SHOP_TIMEZONE)


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def shop_now() -> datetime:
    return datetime.now(SHOP_TZ)


def utc_to_shop(dt: datetime) -> datetime:
    return ensure_utc(dt).astimezone(SHOP_TZ)


def shop_local_to_utc(on_date: date, t: time) -> datetime:
    return datetime.combine(on_date, t, tzinfo=SHOP_TZ).astimezone(timezone.utc)


def horizon_end_utc(anchor_utc: datetime) -> datetime:
    anchor_shop = utc_to_shop(anchor_utc)
    end_shop = anchor_shop + timedelta(days=SCHEDULE_HORIZON_DAYS)
    return end_shop.astimezone(timezone.utc)


def merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    sorted_iv = sorted((ensure_utc(s), ensure_utc(e)) for s, e in intervals if e > s)
    if not sorted_iv:
        return []
    merged = [sorted_iv[0]]
    for start, end in sorted_iv[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def intersect_intervals(
    a: list[tuple[datetime, datetime]],
    b: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    result = []
    for a_start, a_end in merge_intervals(a):
        for b_start, b_end in merge_intervals(b):
            start = max(a_start, b_start)
            end = min(a_end, b_end)
            if end > start:
                result.append((start, end))
    return merge_intervals(result)


def subtract_intervals(
    free: list[tuple[datetime, datetime]],
    busy: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    segments = merge_intervals(free)
    for b_start, b_end in merge_intervals(busy):
        next_segments = []
        for s, e in segments:
            if b_end <= s or b_start >= e:
                next_segments.append((s, e))
            else:
                if s < b_start:
                    next_segments.append((s, b_start))
                if b_end < e:
                    next_segments.append((b_end, e))
        segments = next_segments
    return merge_intervals(segments)


def clip_intervals_to_horizon(
    intervals: list[tuple[datetime, datetime]],
    anchor_utc: datetime,
    end_utc: datetime,
) -> list[tuple[datetime, datetime]]:
    anchor_utc = ensure_utc(anchor_utc)
    end_utc = ensure_utc(end_utc)
    clipped = []
    for start, end in intervals:
        s = max(start, anchor_utc)
        e = min(end, end_utc)
        if e > s:
            clipped.append((s, e))
    return merge_intervals(clipped)


def total_placeable_hours(
    intervals: list[tuple[datetime, datetime]],
    not_before: datetime,
    end_utc: datetime,
) -> float:
    not_before = ensure_utc(not_before)
    end_utc = ensure_utc(end_utc)
    total_seconds = 0.0
    for start, end in intervals:
        s = max(start, not_before)
        e = min(end, end_utc)
        if e > s:
            total_seconds += (e - s).total_seconds()
    return total_seconds / 3600.0


def place_duration(
    intervals: list[tuple[datetime, datetime]],
    duration: timedelta,
    not_before: datetime,
    end_utc: datetime,
) -> tuple[datetime | None, datetime | None, float]:
    """
    Place contiguous working time summing to `duration` within merged free intervals.
    Returns (start_utc, end_utc, placeable_hours_within_horizon).
    """
    not_before = ensure_utc(not_before)
    end_utc = ensure_utc(end_utc)
    remaining = duration.total_seconds()
    if remaining <= 0:
        return not_before, not_before, 0.0

    placeable_hours = total_placeable_hours(intervals, not_before, end_utc)
    start_result = None
    end_result = None

    for seg_start, seg_end in merge_intervals(intervals):
        if seg_end <= not_before:
            continue
        seg_start_eff = max(seg_start, not_before)
        if seg_start_eff >= end_utc:
            break
        seg_end_eff = min(seg_end, end_utc)
        available = (seg_end_eff - seg_start_eff).total_seconds()
        if available <= 0:
            continue
        if start_result is None:
            start_result = seg_start_eff
        take = min(available, remaining)
        remaining -= take
        end_result = seg_start_eff + timedelta(seconds=take)
        if remaining <= 1e-9:
            return start_result, end_result, placeable_hours

    return None, None, placeable_hours


# Proposed starts fall on the half hour and bookings run in half-hour blocks,
# so a plan reads 8:00 AM - 1:30 PM, not 8:26 AM - 1:44 PM.
BOOKING_MINUTES = 30


def round_up_to_block(dt: datetime, minutes: int = BOOKING_MINUTES) -> datetime:
    """``dt`` moved forward to the next whole block of ``minutes`` (shop time)."""
    dt = ensure_utc(dt)
    local = utc_to_shop(dt)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    block = timedelta(minutes=minutes)
    steps = -(-(local - midnight) // block)
    return ensure_utc((midnight + steps * block).astimezone(timezone.utc))


def booked_duration(duration: timedelta, minutes: int = BOOKING_MINUTES) -> timedelta:
    """``duration`` rounded up to whole blocks (5.3 h is booked as 5.5 h)."""
    block = timedelta(minutes=minutes)
    return -(-duration // block) * block


def _hours_on_day(windows, on_date) -> float:
    return sum(
        (min(e, shop_local_to_utc(on_date + timedelta(days=1), time(0, 0)))
         - max(s, shop_local_to_utc(on_date, time(0, 0)))).total_seconds()
        for s, e in windows
        if utc_to_shop(s).date() <= on_date <= utc_to_shop(e - timedelta(seconds=1)).date()
    ) / 3600


def _next_day_start(windows, after: datetime):
    """First working moment on a later shop day than ``after``."""
    day = utc_to_shop(after).date()
    for s, _e in windows:
        if utc_to_shop(s).date() > day:
            return s
    return None


def place_unbroken(
    windows: list[tuple[datetime, datetime]],
    busy: list[tuple[datetime, datetime]],
    duration: timedelta,
    not_before: datetime,
    end_utc: datetime,
    tidy: bool = True,
) -> tuple[datetime | None, datetime | None, float]:
    """
    Earliest placement of `duration` across working `windows` that never touches
    `busy`. Work may stop outside the windows (nights, days off), never for a
    booking: only the start and end are stored, and every working moment between
    them counts as booked. Returns (start_utc, end_utc, free_hours_within_horizon).

    With ``tidy`` (the default) the start falls on the half hour, the booking
    runs in half-hour blocks, and an operation that fits in one working day is
    not split across two: it waits for the next day's start instead.
    """
    windows = merge_intervals(windows)
    busy = merge_intervals(busy)
    placeable = total_placeable_hours(subtract_intervals(windows, busy), not_before, end_utc)
    if tidy:
        duration = booked_duration(duration)
    cursor = round_up_to_block(not_before) if tidy else ensure_utc(not_before)
    hours = duration.total_seconds() / 3600
    while True:
        start, end, _ = place_duration(windows, duration, cursor, end_utc)
        if start is None:
            return None, None, placeable
        if tidy:
            snapped = round_up_to_block(start)
            if snapped != start:
                cursor = snapped
                continue
            last_day = utc_to_shop(end - timedelta(seconds=1)).date()
            if last_day != utc_to_shop(start).date():
                nxt = _next_day_start(windows, start)
                if nxt is not None and nxt < end_utc and hours <= _hours_on_day(windows, utc_to_shop(nxt).date()) + 1e-9:
                    cursor = nxt
                    continue
        worked = intersect_intervals([(start, end)], windows)
        hit = next(
            (b for b in busy if any(b[0] < we and ws < b[1] for ws, we in worked)),
            None,
        )
        if hit is None:
            return start, end, placeable
        cursor = round_up_to_block(hit[1]) if tidy else hit[1]


def derive_working_segments(
    start_utc: datetime,
    end_utc: datetime,
    schedule_by_dow,
    exceptions_by_date,
) -> list[tuple[datetime, datetime]]:
    """
    Read-time only: intersect [scheduled_start, scheduled_end] with the worker's
    working windows. Single source of truth for derived segments — do not
    reimplement this intersection elsewhere.
    """
    start_utc = ensure_utc(start_utc)
    end_utc = ensure_utc(end_utc)
    if end_utc <= start_utc:
        return []
    windows = build_worker_working_windows(
        schedule_by_dow, exceptions_by_date, start_utc, end_utc
    )
    return intersect_intervals([(start_utc, end_utc)], windows)


def serialize_segments(
    segments: list[tuple[datetime, datetime]],
) -> list[dict[str, str]]:
    return [
        {"start": ensure_utc(s).isoformat(), "end": ensure_utc(e).isoformat()}
        for s, e in segments
    ]


def _merge_time_windows(windows: list[tuple[time, time]]) -> list[tuple[time, time]]:
    """Merge overlapping or touching same-day windows; real gaps stay gaps."""
    valid = sorted((s, e) for s, e in windows if s is not None and e is not None and s < e)
    merged: list[tuple[time, time]] = []
    for s, e in valid:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def shop_break() -> tuple[time, time]:
    """The daily break (shop-local start, end) from shop settings; 12:00-13:00
    until the Admin changes it."""
    cache = _request_cache()
    if cache is not None and "break" in cache:
        return cache["break"]
    value = (DEFAULT_BREAK_START, DEFAULT_BREAK_END)
    if has_app_context():
        from app.extensions import db

        with db.session.no_autoflush:
            row = db.session.get(ShopSettings, 1)
        if row is not None and row.break_start and row.break_end and row.break_start < row.break_end:
            value = (row.break_start, row.break_end)
    if cache is not None:
        cache["break"] = value
    return value


def _without_break(windows: list[tuple[time, time]]) -> list[tuple[time, time]]:
    break_start, break_end = shop_break()
    out = []
    for s, e in windows:
        if break_end <= s or break_start >= e:
            out.append((s, e))
            continue
        if s < break_start:
            out.append((s, break_start))
        if break_end < e:
            out.append((break_end, e))
    return out


def break_intervals_utc(start_utc: datetime, end_utc: datetime) -> list[tuple[datetime, datetime]]:
    """The daily break on every shop date touching [start, end), in UTC."""
    start_utc, end_utc = ensure_utc(start_utc), ensure_utc(end_utc)
    if end_utc <= start_utc:
        return []
    break_start, break_end = shop_break()
    out = []
    cur = utc_to_shop(start_utc).date()
    last = utc_to_shop(end_utc).date()
    while cur <= last:
        out.append((shop_local_to_utc(cur, break_start), shop_local_to_utc(cur, break_end)))
        cur += timedelta(days=1)
    return out


def hours_excluding_break(start_utc: datetime, end_utc: datetime) -> float:
    """Clock hours in [start, end) minus the daily break."""
    start_utc, end_utc = ensure_utc(start_utc), ensure_utc(end_utc)
    if end_utc <= start_utc:
        return 0.0
    kept = subtract_intervals([(start_utc, end_utc)], break_intervals_utc(start_utc, end_utc))
    return sum((e - s).total_seconds() for s, e in kept) / 3600.0


def effective_windows_for_date(on_date, schedule_by_dow, exceptions_by_date):
    """
    Working windows (shop-local times) for one calendar date, applying
    WorkerSchedule + WorkCalendarException, minus the daily break. Overtime that
    does not touch the regular shift (e.g. 08–17 plus 18–20) yields separate windows.
    """
    dow = on_date.weekday()  # 0=Mon … 6=Sun
    sched = schedule_by_dow.get(dow)
    exc = exceptions_by_date.get(on_date)

    if exc and exc.type == CalendarExceptionType.HOLIDAY_NO_WORK:
        return []

    base_working = bool(sched and sched.is_working and sched.start_time and sched.end_time)
    base = [(sched.start_time, sched.end_time)] if base_working else []

    if exc and exc.type in (
        CalendarExceptionType.OVERTIME,
        CalendarExceptionType.SPECIAL_WORKING_DAY,
    ):
        if exc.start_time and exc.end_time:
            return _without_break(_merge_time_windows(base + [(exc.start_time, exc.end_time)]))
        if not base_working:
            return _without_break([(time(8, 0), time(17, 0))])

    return _without_break(_merge_time_windows(base))


def effective_hours_for_date(on_date, schedule_by_dow, exceptions_by_date):
    """
    Return (start_time, end_time, is_working) for a shop-local calendar date:
    the outer bounds of effective_windows_for_date (gaps are not reported here).
    """
    windows = effective_windows_for_date(on_date, schedule_by_dow, exceptions_by_date)
    if not windows:
        return None, None, False
    return windows[0][0], windows[-1][1], True


def build_worker_working_windows(
    schedule_by_dow,
    exceptions_by_date,
    anchor_utc: datetime,
    end_utc: datetime,
) -> list[tuple[datetime, datetime]]:
    """Expand worker schedule + calendar exceptions into UTC working intervals.
    No working hours set means no working time, even on overtime days."""
    if not has_working_hours(schedule_by_dow):
        return []
    anchor_shop = utc_to_shop(anchor_utc)
    end_shop = utc_to_shop(end_utc)
    windows = []
    cur = anchor_shop.date()
    last = end_shop.date()
    while cur <= last:
        for day_start_t, day_end_t in effective_windows_for_date(
            cur, schedule_by_dow, exceptions_by_date
        ):
            w_start = shop_local_to_utc(cur, day_start_t)
            w_end = shop_local_to_utc(cur, day_end_t)
            if w_end > anchor_utc and w_start < end_utc:
                windows.append((max(w_start, anchor_utc), min(w_end, end_utc)))
        cur += timedelta(days=1)
    return merge_intervals(windows)


_CACHE_KEY = "_schedule_calendar_cache"
# Extra days loaded around a requested exception range so later, slightly
# different ranges in the same request are answered from the cache.
_EXCEPTION_PAD_DAYS = 62


def _request_cache() -> dict | None:
    """Per-app-context cache, cleared by the session hooks below on any write."""
    if not has_app_context():
        return None
    cache = g.get(_CACHE_KEY)
    if cache is None:
        cache = {"schedules": {}, "schedules_loaded": False, "exceptions": None}
        setattr(g, _CACHE_KEY, cache)
    return cache


def clear_calendar_cache() -> None:
    if has_app_context():
        g.pop(_CACHE_KEY, None)


def _clear_on_relevant_flush(session, _flush_context):
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, (WorkerSchedule, WorkCalendarException, ShopSettings)):
            clear_calendar_cache()
            return


def _clear_on_bulk_write(orm_execute_state):
    if orm_execute_state.is_update or orm_execute_state.is_delete:
        clear_calendar_cache()


event.listen(Session, "after_flush", _clear_on_relevant_flush)
event.listen(Session, "after_commit", lambda _s: clear_calendar_cache())
event.listen(Session, "after_soft_rollback", lambda _s, _t: clear_calendar_cache())
event.listen(Session, "do_orm_execute", _clear_on_bulk_write)


def load_worker_schedule_maps(worker_id):
    return dict(load_worker_schedule_maps_many([worker_id]).get(worker_id, {}))


def has_working_hours(schedule_by_dow) -> bool:
    return any(
        d and d.is_working and d.start_time and d.end_time
        for d in (schedule_by_dow or {}).values()
    )


def crew_schedule_map(maps):
    """The hours a whole crew works together: per weekday, working only when
    every member works, from the latest start to the earliest end. One map is
    returned unchanged. A member with no working hours makes the whole crew
    unavailable (an empty map)."""
    maps = list(maps or [])
    if not maps or not all(has_working_hours(m) for m in maps):
        return {}
    if len(maps) == 1:
        return dict(maps[0])
    out = {}
    for dow in range(7):
        days = [m.get(dow) for m in maps]
        if not all(d and d.is_working and d.start_time and d.end_time for d in days):
            out[dow] = SimpleNamespace(is_working=False, start_time=None, end_time=None)
            continue
        start = max(d.start_time for d in days)
        end = min(d.end_time for d in days)
        out[dow] = SimpleNamespace(
            is_working=start < end, start_time=start if start < end else None,
            end_time=end if start < end else None,
        )
    return out


def load_crew_schedule_map(worker_ids):
    """Shared working hours of a crew (lead first). Empty for no workers."""
    ids = [w for w in (worker_ids or []) if w]
    if not ids:
        return {}
    by_worker = load_worker_schedule_maps_many(ids)
    return crew_schedule_map([by_worker.get(w, {}) for w in ids])


def load_worker_schedule_maps_many(worker_ids):
    """Batch-load WorkerSchedule rows for many workers (one query, cached per request)."""
    ids = [wid for wid in {*(worker_ids or [])} if wid]
    if not ids:
        return {}
    cache = _request_cache()
    if cache is None:
        rows = WorkerSchedule.query.filter(WorkerSchedule.worker_id.in_(ids)).all()
        by_worker = {wid: {} for wid in ids}
        for s in rows:
            by_worker[s.worker_id][s.day_of_week] = s
        return by_worker
    if not cache.get("schedules_loaded"):
        # Seven rows per worker, so one query for everyone beats one per worker.
        by_worker: dict = {}
        for s in WorkerSchedule.query.all():
            by_worker.setdefault(s.worker_id, {})[s.day_of_week] = s
        cache["schedules"] = by_worker
        cache["schedules_loaded"] = True
    cached = cache["schedules"]
    return {wid: dict(cached.get(wid, {})) for wid in ids}


def shop_day_windows_union(period_from: date, period_to: date) -> list[dict]:
    """
    Per calendar date, union of all active workers' effective working hours
    (WorkerSchedule + WorkCalendarException). Used for compressed week-view columns.
    """
    from app.services.worker_profile_service import query_assignable_workers

    workers = query_assignable_workers().all()
    exceptions = load_calendar_exceptions(period_from, period_to)
    schedules_by_worker = load_worker_schedule_maps_many([w.id for w in workers])

    out = []
    cur = period_from
    while cur <= period_to:
        starts: list[time] = []
        ends: list[time] = []
        working = False
        for worker in workers:
            sched = schedules_by_worker.get(worker.id, {})
            day_start, day_end, is_working = effective_hours_for_date(
                cur, sched, exceptions
            )
            if is_working and day_start and day_end:
                working = True
                starts.append(day_start)
                ends.append(day_end)
        if working:
            out.append(
                {
                    "date": cur.isoformat(),
                    "startTime": min(starts).strftime("%H:%M"),
                    "endTime": max(ends).strftime("%H:%M"),
                    "isWorking": True,
                }
            )
        else:
            out.append(
                {
                    "date": cur.isoformat(),
                    "startTime": None,
                    "endTime": None,
                    "isWorking": False,
                }
            )
        cur += timedelta(days=1)
    return out


def worker_day_windows(worker_id: str, period_from: date, period_to: date) -> list[dict]:
    """
    Per calendar date, one worker's effective hours (WorkerSchedule + exceptions).
    Used for the personal production-worker schedule grid.
    """
    exceptions = load_calendar_exceptions(period_from, period_to)
    schedule_by_dow = load_worker_schedule_maps(worker_id)
    out = []
    cur = period_from
    while cur <= period_to:
        day_start, day_end, is_working = effective_hours_for_date(
            cur, schedule_by_dow, exceptions
        )
        if is_working and day_start and day_end:
            out.append(
                {
                    "date": cur.isoformat(),
                    "startTime": day_start.strftime("%H:%M"),
                    "endTime": day_end.strftime("%H:%M"),
                    "isWorking": True,
                }
            )
        else:
            out.append(
                {
                    "date": cur.isoformat(),
                    "startTime": None,
                    "endTime": None,
                    "isWorking": False,
                }
            )
        cur += timedelta(days=1)
    return out


def load_calendar_exceptions(start_date: date, end_date: date):
    cache = _request_cache()
    covered = cache["exceptions"] if cache is not None else None
    if covered is None or start_date < covered[0] or end_date > covered[1]:
        lo = start_date - timedelta(days=_EXCEPTION_PAD_DAYS)
        hi = end_date + timedelta(days=_EXCEPTION_PAD_DAYS)
        if covered is not None:
            lo, hi = min(lo, covered[0]), max(hi, covered[1])
        rows = WorkCalendarException.query.filter(
            WorkCalendarException.date >= lo,
            WorkCalendarException.date <= hi,
        ).all()
        covered = (lo, hi, {e.date: e for e in rows})
        if cache is not None:
            cache["exceptions"] = covered
    return {d: e for d, e in covered[2].items() if start_date <= d <= end_date}


class _ShopSchedule(dict):
    """The default shop week; marked so working-day answers can be memoised."""

    is_default_shop_week = True


def shop_working_day_memo() -> dict | None:
    """Per-request {date: is working day} for the default shop week (calendar
    exceptions applied). None outside a request."""
    cache = _request_cache()
    if cache is None:
        return None
    return cache.setdefault("shop_working_day", {})


def is_shop_working_day(on_date, schedule_by_dow, exceptions_by_date, memo=None) -> bool:
    """effective_windows_for_date(...) is non-empty, memoised for the default
    shop week (exceptions are the same for a date within a request)."""
    if memo is not None and getattr(schedule_by_dow, "is_default_shop_week", False):
        hit = memo.get(on_date)
        if hit is None:
            hit = memo[on_date] = bool(
                effective_windows_for_date(on_date, schedule_by_dow, exceptions_by_date)
            )
        return hit
    return bool(effective_windows_for_date(on_date, schedule_by_dow, exceptions_by_date))


def default_shop_schedule_by_dow():
    """Mon–Sat 08:00–17:00; Sunday off. Shared shop-hours baseline for capacity."""
    out = _ShopSchedule()
    for dow in range(7):
        working = dow < 6
        out[dow] = type(
            "Sched",
            (),
            {
                "is_working": working,
                "start_time": time(8, 0) if working else None,
                "end_time": time(17, 0) if working else None,
            },
        )()
    return out


def next_shop_working_day(on_date: date) -> date:
    """``on_date`` if the shop works that day, else the next day it does
    (skipping Sundays and holidays; a special working day counts as working)."""
    schedule = default_shop_schedule_by_dow()
    exceptions = load_calendar_exceptions(on_date, on_date + timedelta(days=31))
    d = on_date
    for _ in range(32):
        if effective_windows_for_date(d, schedule, exceptions):
            return d
        d += timedelta(days=1)
    return on_date


def shop_available_hours(period_from: date, period_to: date) -> float:
    """
    Available shop hours per calendar day from default shop working hours
    plus WorkCalendarException (same effective_windows_for_date path as the scheduler).
    Overtime / special days add capacity; holiday / no-work days remove it.
    """
    if period_to < period_from:
        return 0.0
    schedule = default_shop_schedule_by_dow()
    exceptions = load_calendar_exceptions(period_from, period_to)
    hours = 0.0
    d = period_from
    while d <= period_to:
        for start_t, end_t in effective_windows_for_date(d, schedule, exceptions):
            start_dt = datetime.combine(d, start_t)
            end_dt = datetime.combine(d, end_t)
            hours += (end_dt - start_dt).total_seconds() / 3600.0
        d += timedelta(days=1)
    return hours


def shop_working_intervals(start_utc: datetime, end_utc: datetime) -> list[tuple[datetime, datetime]]:
    """Shop working time (default hours plus calendar exceptions) inside [start, end)."""
    start_utc, end_utc = ensure_utc(start_utc), ensure_utc(end_utc)
    if end_utc <= start_utc:
        return []
    exceptions = load_calendar_exceptions(
        utc_to_shop(start_utc).date(), utc_to_shop(end_utc).date()
    )
    return build_worker_working_windows(
        default_shop_schedule_by_dow(), exceptions, start_utc, end_utc
    )


def shop_working_hours(intervals: list[tuple[datetime, datetime]]) -> float:
    """Working hours (shop calendar) covered by the given clock intervals."""
    merged = merge_intervals(intervals)
    if not merged:
        return 0.0
    working = shop_working_intervals(merged[0][0], merged[-1][1])
    return sum(
        (e - s).total_seconds() for s, e in intersect_intervals(merged, working)
    ) / 3600.0


def full_horizon_interval(anchor_utc: datetime, end_utc: datetime) -> list[tuple[datetime, datetime]]:
    return [(ensure_utc(anchor_utc), ensure_utc(end_utc))]
