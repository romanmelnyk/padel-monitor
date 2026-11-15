#!/usr/bin/env python3
"""Падел-монітор для twojtenis.pl.

Кожен запуск (за розкладом у GitHub Actions):
  1. завантажує корти клубу і публічні бронювання на N днів уперед;
  2. рахує вільні клітинки (по slot_minutes хвилин) на кожному корті;
  3. порівнює з попереднім станом (data/state.json) і дописує зміни в data/events.csv;
  4. надсилає в Telegram нові вільні блоки, що підходять під фільтр "alerts".

Події в events.csv:
  initial  - перший запуск, стан "як є";
  freed    - клітинка звільнилася (скасування) в даті, яку вже відстежували;
  opened   - клітинка з'явилася, бо відкрився новий день для бронювання;
  taken    - вільна клітинка стала зайнятою (free_min = скільки хвилин була вільною);
  expired  - клітинка так і лишилась вільною до початку гри.
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
EVENT_FIELDS = ["ts", "slot_date", "slot_time", "court", "event", "lead_min", "free_min"]
RUN_FIELDS = ["ts", "gap_min", "courts", "bookings", "excludes", "free_cells", "free_cells_target",
              "blocks_target", "events"]
USER_AGENT = "padel-slot-monitor/1.0"
ERROR_ALERT_AFTER = 6  # стільки невдалих запусків поспіль -> одне попередження в Telegram


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


def http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
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


# ---------------------------------------------------------------- data

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


def collect(cfg: dict, tz: ZoneInfo, now: datetime) -> dict:
    api = cfg["api_base"].rstrip("/")
    club_id = cfg["club_id"]
    club = http_json(f"{api}/Clubs/{club_id}")
    service = http_json(f"{api}/Clubs/{club_id}/technical-group")["serviceUrl"].rstrip("/")

    courts = [
        loc for loc in club.get("locations", [])
        if loc.get("type") == cfg["court_type"] and loc.get("isEnabled", True)
    ]
    courts.sort(key=lambda c: (c.get("sortNumber") or 0, c.get("name", "")))
    if not courts:
        raise RuntimeError(f"у клубі немає кортів типу {cfg['court_type']}")
    court_ids = [c["id"] for c in courts]

    days = [now.date() + timedelta(days=i) for i in range(cfg["days_ahead"] + 1)]
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
            log(f"excludes {iso}: {exc}")
            continue
        for ex in items:
            log(f"exclude {iso}: {json.dumps(ex, ensure_ascii=False)[:300]}")
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
            t = open_m
            while t + step <= close_m:
                if slot_start(iso, to_hm(t), tz) > now and not any(s < t + step and e > t for s, e in taken):
                    free.add(cell_key(iso, t, court["name"]))
                t += step

    return {
        "free": free,
        "window": [d.isoformat() for d in days],
        "courts": len(courts),
        "bookings": n_bookings,
        "excludes": n_excludes,
    }


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


def fmt_block(block: dict) -> str:
    d = date.fromisoformat(block["day"])
    return f"• {UA_DAYS[d.weekday()]} {d:%d.%m} {to_hm(block['start'])}–{to_hm(block['end'])} · {block['court']}"


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
    state = load_state()
    last_run = datetime.fromisoformat(state["last_run"]) if state.get("last_run") else None
    gap = minutes_between(last_run, now) if last_run else ""

    try:
        snap = collect(cfg, tz, now)
    except Exception as exc:  # noqa: BLE001 - мережа/API: не валимо workflow щоразу
        log(f"ПОМИЛКА збору даних: {type(exc).__name__}: {exc}")
        state["errors"] = int(state.get("errors", 0)) + 1
        if state["errors"] == ERROR_ALERT_AFTER:
            send_telegram(f"⚠️ Падел-монітор: {ERROR_ALERT_AFTER} невдалих запусків поспіль ({type(exc).__name__}). Перевірте GitHub Actions.")
        save_state(state)
        append_csv(RUNS_FILE, RUN_FIELDS, [{"ts": ts, "gap_min": gap, "events": "error"}])
        return 0

    free_now: set[str] = snap["free"]
    first_run = "free" not in state
    prev_free: dict[str, str] = state.get("free", {})
    prev_window = set(state.get("window", []))

    events: list[dict] = []
    new_free: dict[str, str] = {}
    new_kind: dict[str, str] = {}

    for key in sorted(free_now):
        if key in prev_free:
            new_free[key] = prev_free[key]
            continue
        new_free[key] = ts
        day, hm, court = split_key(key)
        kind = "initial" if first_run else ("freed" if day in prev_window else "opened")
        new_kind[key] = kind
        events.append({
            "ts": ts, "slot_date": day, "slot_time": hm, "court": court, "event": kind,
            "lead_min": minutes_between(now, slot_start(day, hm, tz)), "free_min": "",
        })

    for key, since in sorted(prev_free.items()):
        if key in free_now:
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
        "ts": ts, "gap_min": gap, "courts": snap["courts"], "bookings": snap["bookings"],
        "excludes": snap["excludes"], "free_cells": len(free_now),
        "free_cells_target": target_now, "blocks_target": len(blocks), "events": len(events),
    }])
    save_state({"last_run": ts, "window": snap["window"], "free": new_free, "errors": 0})

    counts: dict[str, int] = {}
    for e in events:
        counts[e["event"]] = counts.get(e["event"], 0) + 1
    print(f"{ts}: вільно {len(free_now)} клітинок (у ваш час {target_now}); події {counts or '-'}")

    alerts = cfg["alerts"]
    if not alerts.get("enabled"):
        return 0
    url = cfg["booking_url"]
    if first_run:
        lines = [f"✅ Падел-монітор запущено: {cfg['club_name']}."]
        if blocks:
            lines.append(f"Зараз вільно у ваш час ({len(blocks)}):")
            lines += [fmt_block(b) for b in blocks[:25]]
        else:
            lines.append("Зараз у ваш час вільних слотів немає. Напишу, щойно щось звільниться.")
        lines.append(f"Бронювати: {url}")
        send_telegram("\n".join(lines))
        return 0

    notify_kinds = {"freed"} | ({"opened"} if alerts.get("on_window_open") else set())
    fresh = [
        b for b in blocks
        if any(new_kind.get(k) in notify_kinds for k in block_cells(b, cfg["slot_minutes"]))
    ]
    if fresh:
        lines = [f"🎾 {cfg['club_name']}: звільнився корт"]
        lines += [fmt_block(b) for b in fresh]
        lines.append(f"Бронювати: {url}")
        send_telegram("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
