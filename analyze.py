#!/usr/bin/env python3
"""Аналіз журналу data/events.csv + data/runs.csv -> reports/report.md.

Головні питання:
  * скільки насправді звільняється слотів (скасувань) і в який час гри;
  * коли саме вони з'являються (година доби, за скільки до гри);
  * як швидко їх забирають інші;
  * що відбувається, коли відкривається новий день для бронювання.
Одиниця виміру: "год корту" (кількість 30-хв клітинок × 0.5).
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EVENTS_FILE = ROOT / "data" / "events.csv"
RUNS_FILE = ROOT / "data" / "runs.csv"
REPORT_FILE = ROOT / "reports" / "report.md"
UA_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")[:2]
    return int(h) * 60 + int(m)


def to_hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def hours(cells: float, step: int) -> str:
    v = round(cells * step / 60, 1)
    return f"{v:g}"


def bucket(value: int, edges: list[tuple[int, str]], last: str) -> str:
    for limit, label in edges:
        if value < limit:
            return label
    return last


LEAD_EDGES = [(120, "< 2 год"), (360, "2–6 год"), (1440, "6–24 год"), (2880, "1–2 дні"), (5760, "2–4 дні")]
LEAD_LAST = "4–8 днів"
SPEED_EDGES = [(15, "< 15 хв"), (60, "15–60 хв"), (180, "1–3 год"), (720, "3–12 год")]
SPEED_LAST = "> 12 год"


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    step = cfg["slot_minutes"]
    a = cfg["alerts"]
    t_from, t_to = to_min(a["from"]), to_min(a["to"])
    target_desc = f"{', '.join(UA_DAYS[d] for d in a['weekdays'])} {a['from']}–{a['to']}"

    events = load_csv(EVENTS_FILE)
    runs = load_csv(RUNS_FILE)
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)

    md: list[str] = [f"# Падел-аналітика: {cfg['club_name']}", ""]
    md.append(f"Ваш цільовий час: **{target_desc}**. Одиниця: год корту (1 клітинка = {step} хв).")
    md.append("")

    # ------------------------------------------------ покриття
    ok_runs = [r for r in runs if r.get("events") != "error"]
    gaps = [int(r["gap_min"]) for r in ok_runs if r.get("gap_min")]
    md.append("## Покриття даних")
    if not runs:
        md.append("Даних ще немає.")
        REPORT_FILE.write_text("\n".join(md) + "\n", encoding="utf-8")
        return
    md.append(table(["Показник", "Значення"], [
        ["Перший запуск", runs[0]["ts"][:16].replace("T", " ")],
        ["Останній запуск", runs[-1]["ts"][:16].replace("T", " ")],
        ["Успішних запусків", len(ok_runs)],
        ["Помилок", len(runs) - len(ok_runs)],
        ["Медіанний інтервал, хв", statistics.median(gaps) if gaps else "-"],
        ["Найбільший пропуск, хв", max(gaps) if gaps else "-"],
        ["Пропусків > 30 хв", sum(1 for g in gaps if g > 30)],
    ]))
    md.append("")
    md.append("> Скасування, які звільнились і були заброньовані між двома запусками, в журнал не потрапляють, "
              "тому реальна кількість звільнень трохи **більша** за показану.")
    md.append("")

    # ------------------------------------------------ підготовка подій
    origin: dict[tuple, str] = {}
    for r in events:
        r["_ts"] = datetime.fromisoformat(r["ts"])
        d = date.fromisoformat(r["slot_date"])
        m = to_min(r["slot_time"])
        r["_wd"], r["_m"] = d.weekday(), m
        r["_target"] = d.weekday() in a["weekdays"] and t_from <= m and m + step <= t_to
        key = (r["slot_date"], r["slot_time"], r["court"])
        if r["event"] in ("initial", "freed", "opened"):
            origin[key] = r["event"]
        else:
            r["_origin"] = origin.pop(key, "unknown")

    # інциденти = суцільні блоки клітинок, що звільнились в одному запуску на одному корті
    incidents: list[dict] = []
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for r in events:
        if r["event"] == "freed":
            grouped[(r["ts"], r["slot_date"], r["court"])].append(r)
    for (_, day, court), rows in grouped.items():
        rows.sort(key=lambda x: x["_m"])
        cur: list[dict] = []
        for r in rows + [None]:
            if r is not None and cur and r["_m"] == cur[-1]["_m"] + step:
                cur.append(r)
                continue
            if cur:
                incidents.append({
                    "ts": cur[0]["_ts"], "day": day, "court": court, "wd": cur[0]["_wd"],
                    "start": cur[0]["_m"], "end": cur[-1]["_m"] + step, "cells": len(cur),
                    "target_cells": sum(1 for x in cur if x["_target"]),
                    "lead": int(cur[0]["lead_min"]),
                })
            cur = [r] if r is not None else []
    incidents.sort(key=lambda i: i["ts"])
    t_inc = [i for i in incidents if i["target_cells"]]

    # ------------------------------------------------ підсумок
    def cnt(kind: str, target: bool | None = None) -> int:
        return sum(1 for r in events if r["event"] == kind and (target is None or r["_target"] == target))

    md.append("## Підсумок")
    md.append(table(["Подія", "Усього, год", "У ваш час, год"], [
        ["Звільнилось (скасування)", hours(cnt("freed"), step), hours(cnt("freed", True), step)],
        ["Відкрилось з новим днем", hours(cnt("opened"), step), hours(cnt("opened", True), step)],
        ["Заброньовано з вільного", hours(cnt("taken"), step), hours(cnt("taken", True), step)],
        ["Лишилось непроданим", hours(cnt("expired"), step), hours(cnt("expired", True), step)],
    ]))
    md.append("")
    md.append(f"Скасувань (суцільних блоків) усього: **{len(incidents)}**, у ваш час: **{len(t_inc)}**.")
    if ok_runs:
        tgt = [int(r["free_cells_target"]) for r in ok_runs if r.get("free_cells_target")]
        if tgt:
            md.append(f"У середньому в момент перевірки у ваш час було вільно {hours(statistics.mean(tgt), step)} год "
                      f"(максимум {hours(max(tgt), step)}, нуль у {sum(1 for x in tgt if x == 0)} з {len(tgt)} перевірок).")
        blk = [int(r["blocks_target"]) for r in ok_runs if r.get("blocks_target")]
        if blk:
            md.append(f"Суцільний блок ≥ {a['min_duration_minutes']} хв у ваш час був доступний у "
                      f"**{sum(1 for x in blk if x)} з {len(blk)}** перевірок (у середньому {statistics.mean(blk):.1f} блоку).")
    long_inc = [i for i in t_inc if i["target_cells"] * step >= a["min_duration_minutes"]]
    md.append(f"Скасувань у ваш час, що одразу дали блок ≥ {a['min_duration_minutes']} хв: **{len(long_inc)}**.")
    md.append("")

    # ------------------------------------------------ на який час гри звільняються
    md.append("## На який час гри звільняються корти (скасування, к-сть блоків)")
    hrs = sorted({i["start"] // 60 for i in incidents}) or list(range(7, 23))
    heat = Counter((i["start"] // 60, i["wd"]) for i in incidents)
    md.append(table(["Година гри"] + UA_DAYS,
                    [[f"{h:02d}:00"] + [heat.get((h, wd), "") for wd in range(7)] for h in hrs]))
    md.append("")
    by_court = Counter(i["court"] for i in t_inc)
    if by_court:
        md.append("У ваш час за кортами: " + ", ".join(f"{c} — {n}" for c, n in sorted(by_court.items())))
        md.append("")

    # ------------------------------------------------ коли з'являються
    md.append("## Коли з'являються скасування у ваш час")
    hour_cnt = Counter(i["ts"].hour for i in t_inc)
    wd_cnt = Counter(i["ts"].weekday() for i in t_inc)
    md.append(table(["Година доби", "Скасувань"],
                    [[f"{h:02d}:00–{h:02d}:59", hour_cnt[h]] for h in range(24) if hour_cnt[h]] or [["-", 0]]))
    md.append("")
    md.append("За днем, коли скасовують: " +
              (", ".join(f"{UA_DAYS[d]} — {wd_cnt[d]}" for d in range(7) if wd_cnt[d]) or "-"))
    md.append("")
    lead = Counter(bucket(i["lead"], LEAD_EDGES, LEAD_LAST) for i in t_inc)
    md.append("**За скільки до гри звільняється:**")
    md.append("")
    md.append(table(["До початку гри", "Скасувань"],
                    [[lbl, lead[lbl]] for _, lbl in LEAD_EDGES + [(0, LEAD_LAST)]]))
    md.append("")

    # ------------------------------------------------ як швидко забирають
    def speed_table(orig: str) -> str:
        taken = [r for r in events if r["event"] == "taken" and r["_target"] and r.get("_origin") == orig]
        expired = [r for r in events if r["event"] == "expired" and r["_target"] and r.get("_origin") == orig]
        c = Counter(bucket(int(r["free_min"]), SPEED_EDGES, SPEED_LAST) for r in taken)
        rows = [[lbl, hours(c[lbl], step)] for _, lbl in SPEED_EDGES + [(0, SPEED_LAST)]]
        rows.append(["так і не забронювали", hours(len(expired), step)])
        return table(["Через скільки забронювали", "Год корту"], rows)

    md.append("## Як швидко забирають звільнені слоти (ваш час)")
    md.append(speed_table("freed"))
    md.append("")
    md.append("## Відкриття нового дня (ваш час)")
    opened_t = [r for r in events if r["event"] == "opened" and r["_target"]]
    days_opened = len({r["slot_date"] for r in opened_t})
    if days_opened:
        md.append(f"Нових днів: {days_opened}; у середньому вільно у ваш час при відкритті: "
                  f"{hours(len(opened_t) / days_opened, step)} год. Новий день відкривається о "
                  f"{Counter(r['_ts'].strftime('%H:%M') for r in opened_t).most_common(1)[0][0]} (найчастіше).")
        md.append("")
    md.append(speed_table("opened"))
    md.append("")

    # ------------------------------------------------ непродане
    exp = [r for r in events if r["event"] == "expired"]
    if exp:
        md.append("## Що лишається непроданим (год корту)")
        heat_e = Counter((r["_m"] // 60, r["_wd"]) for r in exp)
        hrs_e = sorted({r["_m"] // 60 for r in exp})
        md.append(table(["Година гри"] + UA_DAYS,
                        [[f"{h:02d}:00"] + [hours(heat_e[(h, wd)], step) if heat_e.get((h, wd)) else "" for wd in range(7)]
                         for h in hrs_e]))
        md.append("")

    # ------------------------------------------------ останні скасування
    md.append("## Останні скасування у ваш час")
    rows = []
    for i in reversed(t_inc[-25:]):
        d = date.fromisoformat(i["day"])
        rows.append([i["ts"].strftime("%d.%m %H:%M"), f"{UA_DAYS[d.weekday()]} {d:%d.%m}",
                     f"{to_hm(i['start'])}–{to_hm(i['end'])}", i["court"], f"{i['lead'] / 60:.1f} год"])
    md.append(table(["Помічено", "День гри", "Час", "Корт", "До гри"], rows) if rows else "Ще не було.")
    md.append("")

    REPORT_FILE.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"звіт: {REPORT_FILE}")


if __name__ == "__main__":
    main()
