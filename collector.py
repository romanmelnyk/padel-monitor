#!/usr/bin/env python3
"""Падел-монітор: twojtenis.pl + tenis4u.pl.

Кожен запуск (за розкладом у GitHub Actions):
  1. для кожного клубу з config.json -> sources завантажує розклад;
  2. рахує вільні клітинки (по slot_minutes хвилин) на кожному корті;
  3. порівнює з попереднім станом (data/state.json) і дописує зміни в data/events.csv;
  4. надсилає в Telegram нові вільні блоки, що підходять під фільтр "alerts".

Події в events.csv:
  initial  - перший запуск клубу, стан "як є";
  freed    - клітинка звільнилася (скасування) в даті, яку вже відстежували;
  opened   - клітинка з'явилася, бо відкрився новий день для бронювання;
  taken    - вільна клітинка стала зайнятою (free_min = скільки хвилин була вільною);
  expired  - клітинка так і лишилась вільною до початку гри.

Назва корту в даних: "<short> · <корт>", наприклад "Błonia · Padel 2".
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STATE_FILE = DATA / "state.json"
EVENTS_FILE = DATA / "events.csv"
RUNS_FILE = DATA / "runs.csv"

DAY_KEYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
UA_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
SEP = " · "
EVENT_FIELDS = ["ts", "slot_date", "slot_time", "court", "event", "lead_min", "free_min"]
RUN_FIELDS = ["ts", "gap_min", "courts", "bookings", "excludes", "free_cells", "free_cells_target",
              "blocks_target", "events", "failed"]
USER_AGENT = "padel-slot-monitor/1.0"
ERROR_ALERT_AFTER = 15  # стільки невдалих перевірок клубу поспіль (~30 хв) -> одне попередження в Telegram


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------- helpers

def to_min(hhmm: str) -> int:
    h, m = str(hhmm).split(":")[:2]
    return int(h) * 60 + int(m)


def to_hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def cell_key(day: str, minutes: int, court: str) -> str:
    return f"{day}|{to_hm(minutes)}|{court}"


def split_key(key: str) -> tuple[str, str, str]:
    day, hm, court = key.split("|", 2)
    return day, hm, court


def source_of(key: str) -> str:
    return split_key(key)[2].split(SEP, 1)[0]


def court_label(src: dict, name: str) -> str:
    return f"{src['short']}{SEP}{name}"


def slot_start(day: str, hm: str, tz: ZoneInfo) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}").replace(tzinfo=tz)


def minutes_between(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds() // 60)


def in_target(day: str, minutes: int, cfg: dict) -> bool:
    alerts = cfg["alerts"]
    weekday = date.fromisoformat(day).weekday()
    return (
        weekday in alerts["weekdays"]
        and to_min(alerts["from"]) <= minutes
        and minutes + cfg["slot_minutes"] <= to_min(alerts["to"])
    )


def overlaps(t: int, step: int, intervals: list[tuple[int, int]]) -> bool:
    return any(s < t + step and e > t for s, e in intervals)


def http_json(url: str, headers: dict | None = None):
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read()[:200].decode("utf-8", "replace")
            if exc.code < 500 or attempt == 2:
                raise RuntimeError(f"HTTP {exc.code} {url}: {body}") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == 2:
                raise
            log(f"retry {attempt + 1} for {url}: {exc}")
        time.sleep(5 * (attempt + 1))
    return None


def send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("[telegram не налаштовано, повідомлення:]\n" + text)
        return
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=30):
            pass
    except urllib.error.HTTPError as exc:  # не друкуємо URL: у ньому токен
        log(f"telegram: HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001
        log(f"telegram: {type(exc).__name__}")


# ---------------------------------------------------------------- twojtenis.pl

def exclude_intervals(ex, day: str, court_ids: list[str]) -> list[tuple[str, int, int]]:
    """Закриття корту (excludes). Формат API не задокументований, розбираємо обережно."""
    if not isinstance(ex, dict):
        return []
    if str(ex.get("date") or day)[:10] != day:
        return []
    if ex.get("locationIds") or ex.get("locations"):
        raw = ex.get("locationIds") or ex.get("locations")
        ids = [i if isinstance(i, str) else (i or {}).get("id") for i in raw]
    elif ex.get("locationId"):
        ids = [ex["locationId"]]
    else:
        ids = list(court_ids)
    start = to_min(ex["startTime"]) if ex.get("startTime") else 0
    end = to_min(ex["endTime"]) if ex.get("endTime") else 24 * 60
    if end <= start:
        end = 24 * 60
    return [(cid, start, end) for cid in ids if cid in court_ids]


def collect_twojtenis(src: dict, cfg: dict, tz: ZoneInfo, now: datetime) -> dict:
    api = src["api_base"].rstrip("/")
    club_id = src["club_id"]
    club = http_json(f"{api}/Clubs/{club_id}")
    service = http_json(f"{api}/Clubs/{club_id}/technical-group")["serviceUrl"].rstrip("/")

    courts = [
        loc for loc in club.get("locations", [])
        if loc.get("type") == src["court_type"] and loc.get("isEnabled", True)
    ]
    if not courts:
        raise RuntimeError(f"у клубі немає кортів типу {src['court_type']}")
    court_ids = [c["id"] for c in courts]

    days = [now.date() + timedelta(days=i) for i in range(src["days_ahead"] + 1)]
    query = urllib.parse.urlencode({"from": days[0].isoformat(), "to": days[-1].isoformat()})
    bookings = http_json(f"{service}/api/v1/Clubs/{club_id}/bookings/public?{query}") or []

    busy: dict[tuple[str, str], list[tuple[int, int]]] = {}
    n_bookings = 0
    for b in bookings:
        if b.get("locationId") not in court_ids:
            continue
        start, end = to_min(b["startTime"]), to_min(b["endTime"])
        if end <= start:
            end = 24 * 60
        busy.setdefault((str(b["date"])[:10], b["locationId"]), []).append((start, end))
        n_bookings += 1

    n_excludes = 0
    for d in days:
        iso = d.isoformat()
        try:
            items = http_json(f"{service}/api/v1/clubs/{club_id}/excludes/public?date={iso}") or []
        except Exception as exc:  # noqa: BLE001
            log(f"{src['short']}: excludes {iso}: {exc}")
            continue
        for ex in items:
            log(f"{src['short']}: exclude {iso}: {json.dumps(ex, ensure_ascii=False)[:300]}")
            for cid, start, end in exclude_intervals(ex, iso, court_ids):
                busy.setdefault((iso, cid), []).append((start, end))
                n_excludes += 1

    step = cfg["slot_minutes"]
    free: set[str] = set()
    for d in days:
        iso = d.isoformat()
        for court in courts:
            hours = (court.get("openHours") or {}).get(DAY_KEYS[d.weekday()]) or {}
            if not hours.get("from") or not hours.get("to"):
                continue
            open_m, close_m = to_min(hours["from"]), to_min(hours["to"])
            if close_m <= open_m:
                close_m = 24 * 60
            taken = busy.get((iso, court["id"]), [])
            label = court_label(src, court["name"])
            t = open_m
            while t + step <= close_m:
                if slot_start(iso, to_hm(t), tz) > now and not overlaps(t, step, taken):
                    free.add(cell_key(iso, t, label))
                t += step

    return {"free": free, "window": [d.isoformat() for d in days], "courts": len(courts),
            "bookings": n_bookings, "excludes": n_excludes}


# ---------------------------------------------------------------- tenis4u.pl

def t4u_interval(x, day: str = "") -> tuple[int, int] | None:
    if not isinstance(x, dict):
        return None
    if x.get("starts_at") and x.get("ends_at") and day:  # блокування: "2026-11-28T07:00:00"
        start_dt = datetime.fromisoformat(str(x["starts_at"])[:19])
        end_dt = datetime.fromisoformat(str(x["ends_at"])[:19])
        day_start = datetime.fromisoformat(f"{day}T00:00:00")
        s = max(minutes_between(day_start, start_dt), 0)
        e = min(minutes_between(day_start, end_dt), 24 * 60)
        return (s, e) if e > s else None
    b, e = x.get("begin"), x.get("end")
    if isinstance(b, dict) and isinstance(e, dict):
        return b.get("hour", 0) * 60 + b.get("minute", 0), e.get("hour", 0) * 60 + e.get("minute", 0)
    if x.get("begin_time") and x.get("end_time"):
        end = to_min(x["end_time"])
        return to_min(x["begin_time"]), 24 * 60 if end == 23 * 60 + 59 else end
    return None


def t4u_court_name(station: dict) -> str:
    name = (station.get("name") or f"kort {station.get('id')}").strip()
    name = name.split(": ", 1)[-1].strip()  # "hala: padel 1" -> "padel 1"
    return name[:1].upper() + name[1:]


def collect_tenis4u(src: dict, cfg: dict, tz: ZoneInfo, now: datetime) -> dict:
    api = src.get("api_base", "https://api.tenis4u.pl").rstrip("/")
    occ = http_json(f"{api}/occupancy/{src['court_id']}", {"X-Tenis-User-Agent": src["client_header"]})
    step = cfg["slot_minutes"]
    # клуб без бронювання з пів години: рахуємо лише цілі години (обидві половини вільні)
    full_hours = src.get("full_hours_only") or not (occ.get("court_data") or {}).get("allow_half_hour", True)
    unit = 60 if full_hours else step
    last_day = now.date() + timedelta(days=src["days_ahead"])
    free: set[str] = set()
    window: set[str] = set()
    n_courts = n_blockades = 0

    excluded = set(src.get("exclude_station_ids", []))
    for st in occ.get("stations", []):
        if src.get("station_icon") and st.get("icon") != src["station_icon"]:
            continue
        if st.get("id") in excluded:
            continue
        n_courts += 1
        label = court_label(src, t4u_court_name(st))
        for day in st.get("days", []):
            iso = str(day.get("date", "")).replace("/", "-")[:10]
            try:
                d = date.fromisoformat(iso)
            except ValueError:
                continue
            if d < now.date() or d > last_day:
                continue
            window.add(iso)
            blocked = [iv for iv in (t4u_interval(b, iso) for b in day.get("blockades") or []) if iv]
            n_blockades += len(blocked)
            for fh in day.get("free_hours") or []:
                iv = t4u_interval(fh)
                if not iv:
                    continue
                start, end = iv
                t = -(-start // unit) * unit  # вирівнюємо на сітку
                while t + unit <= end:
                    if slot_start(iso, to_hm(t), tz) > now and not overlaps(t, unit, blocked):
                        for c in range(t, t + unit, step):
                            free.add(cell_key(iso, c, label))
                    t += unit

    if not n_courts:
        raise RuntimeError("не знайдено кортів (stations) потрібного типу")
    return {"free": free, "window": sorted(window), "courts": n_courts,
            "bookings": "", "excludes": n_blockades}


COLLECTORS = {"twojtenis": collect_twojtenis, "tenis4u": collect_tenis4u}


# ---------------------------------------------------------------- blocks / messages

def target_blocks(cells, cfg: dict) -> list[dict]:
    """Склеює вільні клітинки цільового часу в суцільні блоки >= min_duration_minutes."""
    step = cfg["slot_minutes"]
    grouped: dict[tuple[str, str], list[int]] = {}
    for key in cells:
        day, hm, court = split_key(key)
        m = to_min(hm)
        if in_target(day, m, cfg):
            grouped.setdefault((day, court), []).append(m)

    blocks = []
    for (day, court), mins in grouped.items():
        mins.sort()
        start = prev = mins[0]
        for m in mins[1:] + [None]:
            if m is not None and m == prev + step:
                prev = m
                continue
            if prev + step - start >= cfg["alerts"]["min_duration_minutes"]:
                blocks.append({"day": day, "court": court, "start": start, "end": prev + step})
            if m is not None:
                start = prev = m
    blocks.sort(key=lambda b: (b["day"], b["start"], b["court"]))
    return blocks


def block_cells(block: dict, step: int) -> list[str]:
    return [cell_key(block["day"], t, block["court"]) for t in range(block["start"], block["end"], step)]


def format_blocks(blocks: list[dict], sources: dict[str, dict], limit: int = 25) -> list[str]:
    lines: list[str] = []
    by_src: dict[str, list[dict]] = {}
    for b in blocks:
        by_src.setdefault(b["court"].split(SEP, 1)[0], []).append(b)
    for short, items in by_src.items():
        src = sources.get(short, {})
        lines.append(f"\n{src.get('name', short)}:")
        for b in items[:limit]:
            d = date.fromisoformat(b["day"])
            court = b["court"].split(SEP, 1)[-1]
            lines.append(f"• {UA_DAYS[d.weekday()]} {d:%d.%m} {to_hm(b['start'])}–{to_hm(b['end'])} · {court}")
        if len(items) > limit:
            lines.append(f"…і ще {len(items) - limit}")
        if src.get("booking_url"):
            lines.append(src["booking_url"])
    return lines


# ---------------------------------------------------------------- storage

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def append_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    DATA.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------- main

def main() -> int:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg["timezone"])
    now = datetime.now(tz).replace(second=0, microsecond=0)
    ts = now.isoformat()
    sources = {s["short"]: s for s in cfg["sources"] if s.get("enabled", True)}

    state = load_state()
    last_run = datetime.fromisoformat(state["last_run"]) if state.get("last_run") else None
    gap = minutes_between(last_run, now) if last_run else ""
    prev_free: dict[str, str] = state.get("free", {})
    prev_windows: dict[str, list[str]] = state.get("windows", {})
    seen_sources = set(state.get("sources_seen", []))
    errors: dict[str, int] = state.get("errors", {}) if isinstance(state.get("errors"), dict) else {}

    results: dict[str, dict] = {}
    failed: list[str] = []
    for short, src in sources.items():
        try:
            results[short] = COLLECTORS[src["type"]](src, cfg, tz, now)
            errors[short] = 0
        except Exception as exc:  # noqa: BLE001 - мережа/API: не валимо весь запуск
            log(f"ПОМИЛКА {short}: {type(exc).__name__}: {exc}")
            failed.append(short)
            errors[short] = errors.get(short, 0) + 1
            if errors[short] == ERROR_ALERT_AFTER:
                hint = " Можливо, tenis4u оновив версію застосунку (client_header у config.json)." \
                    if src["type"] == "tenis4u" else ""
                send_telegram(f"⚠️ Падел-монітор: {short} — {ERROR_ALERT_AFTER} невдалих запусків поспіль "
                              f"({type(exc).__name__}).{hint}")

    if not results:
        state["errors"] = errors
        save_state(state)
        append_csv(RUNS_FILE, RUN_FIELDS, [{"ts": ts, "gap_min": gap, "events": "error", "failed": " ".join(failed)}])
        return 0

    # клуби, які зараз не відповіли, лишаємо в попередньому стані (щоб не було хибних "taken")
    free_now: set[str] = set()
    for res in results.values():
        free_now |= res["free"]
    free_now |= {k for k in prev_free if source_of(k) in failed}
    windows = {s: w for s, w in prev_windows.items() if s in sources}
    windows.update({s: r["window"] for s, r in results.items()})

    events: list[dict] = []
    new_free: dict[str, str] = {}
    new_kind: dict[str, str] = {}

    for key in sorted(free_now):
        if key in prev_free:
            new_free[key] = prev_free[key]
            continue
        new_free[key] = ts
        day, hm, court = split_key(key)
        src = source_of(key)
        if src not in seen_sources:
            kind = "initial"
        elif day in prev_windows.get(src, []):
            kind = "freed"
        else:
            kind = "opened"
        new_kind[key] = kind
        events.append({
            "ts": ts, "slot_date": day, "slot_time": hm, "court": court, "event": kind,
            "lead_min": minutes_between(now, slot_start(day, hm, tz)), "free_min": "",
        })

    for key, since in sorted(prev_free.items()):
        if key in free_now or source_of(key) not in sources:
            continue
        day, hm, court = split_key(key)
        start = slot_start(day, hm, tz)
        kind = "expired" if start <= now else "taken"
        events.append({
            "ts": ts, "slot_date": day, "slot_time": hm, "court": court, "event": kind,
            "lead_min": max(minutes_between(now, start), 0),
            "free_min": minutes_between(datetime.fromisoformat(since), now),
        })

    target_now = sum(1 for k in free_now if in_target(split_key(k)[0], to_min(split_key(k)[1]), cfg))
    blocks = target_blocks(free_now, cfg)
    append_csv(EVENTS_FILE, EVENT_FIELDS, events)
    append_csv(RUNS_FILE, RUN_FIELDS, [{
        "ts": ts, "gap_min": gap,
        "courts": sum(r["courts"] for r in results.values()),
        "bookings": sum(int(r["bookings"] or 0) for r in results.values()),
        "excludes": sum(r["excludes"] for r in results.values()),
        "free_cells": len(free_now), "free_cells_target": target_now,
        "blocks_target": len(blocks), "events": len(events), "failed": " ".join(failed),
    }])
    newly_seen = [s for s in results if s not in seen_sources]
    save_state({
        "last_run": ts, "windows": windows, "free": new_free, "errors": errors,
        "sources_seen": sorted(seen_sources | set(results)),
    })

    counts: dict[str, int] = {}
    for e in events:
        counts[e["event"]] = counts.get(e["event"], 0) + 1
    print(f"{ts}: вільно {len(free_now)} клітинок (у ваш час {target_now}, блоків {len(blocks)}); "
          f"події {counts or '-'}; збій: {failed or '-'}")

    alerts = cfg["alerts"]
    if not alerts.get("enabled"):
        return 0
    hours = alerts["min_duration_minutes"] / 60

    if newly_seen:
        start_blocks = [b for b in blocks if b["court"].split(SEP, 1)[0] in newly_seen]
        lines = [f"✅ Падел-монітор стежить за: {', '.join(sources[s]['name'] for s in newly_seen)}."]
        if start_blocks:
            lines.append(f"Зараз вільно у ваш час (від {hours:g} год):")
            lines += format_blocks(start_blocks, sources)
        else:
            lines.append("Зараз у ваш час вільних блоків немає. Напишу, щойно щось звільниться.")
        send_telegram("\n".join(lines))

    notify_kinds = {"freed"} | ({"opened"} if alerts.get("on_window_open") else set())
    fresh = [
        b for b in blocks
        if any(new_kind.get(k) in notify_kinds for k in block_cells(b, cfg["slot_minutes"]))
    ]
    if fresh:
        lines = [f"🎾 Звільнився корт (від {hours:g} год)"]
        lines += format_blocks(fresh, sources)
        send_telegram("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
