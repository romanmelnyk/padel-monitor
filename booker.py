#!/usr/bin/env python3
"""РђРІС‚РѕР±СЂРѕРЅСЋРІР°РЅРЅСЏ РєРѕСЂС‚С–РІ (tenis4u.pl) + РєРЅРѕРїРєРё РІ Telegram.

РџСЂР°РІРёР»Р° (config.json -> booking):
  - РЅРµ Р±С–Р»СЊС€Рµ РѕРґРЅРѕРіРѕ РєРѕСЂС‚Сѓ РЅР° РґРµРЅСЊ (Р±РѕС‚ РЅРµ Р±СЂРѕРЅСЋС” РґРµРЅСЊ, РґРµ РІР¶Рµ С” РІР°С€Р° Р±СЂРѕРЅСЊ Сѓ tenis4u);
  - РґРѕ РіСЂРё >= auto_min_lead_hours (30 РіРѕРґ) -> Р±СЂРѕРЅСЋС” СЃР°Рј С– РїРёС€Рµ, РґРѕ РєРѕР»Рё РјРѕР¶РЅР° Р±РµР·РєРѕС€С‚РѕРІРЅРѕ СЃРєР°СЃСѓРІР°С‚Рё;
  - РјРµРЅС€Рµ -> РЅР°РґСЃРёР»Р°С” РїСЂРѕРїРѕР·РёС†С–СЋ Р· РєРЅРѕРїРєР°РјРё В«Р—Р°Р±СЂРѕРЅСЋРІР°С‚РёВ» / В«РџСЂРѕРїСѓСЃС‚РёС‚РёВ»;
  - С‚СЂРёРІР°Р»С–СЃС‚СЊ: prefer_minutes (2 РіРѕРґ), СЏРєС‰Рѕ Р±Р»РѕРє РґРѕР·РІРѕР»СЏС”, С–РЅР°РєС€Рµ РјС–РЅС–РјР°Р»СЊРЅРёР№ Р±Р»РѕРє (1.5 РіРѕРґ);
  - СЏРєС‰Рѕ РІ РґРµРЅСЊ РєС–Р»СЊРєР° РІР°СЂС–Р°РЅС‚С–РІ -> РЅР°Р№СЂР°РЅС–С€РёР№ С‡Р°СЃ;
  - РЅРµ Р±С–Р»СЊС€Рµ max_active_auto Р°РєС‚РёРІРЅРёС… Р°РІС‚РѕР±СЂРѕРЅРµР№ РѕРґРЅРѕС‡Р°СЃРЅРѕ.

Р—Р°РїСѓСЃРє:
  python booker.py poll SECONDS  - СЃР»СѓС…Р°С” РєРЅРѕРїРєРё Telegram SECONDS СЃРµРєСѓРЅРґ (РјС–Р¶ РїРµСЂРµРІС–СЂРєР°РјРё РІ monitor.yml)
  python booker.py check         - РїРµСЂРµРІС–СЂРєР° РІС…РѕРґСѓ С– С†С–РЅ Р±РµР· Р±СЂРѕРЅСЋРІР°РЅРЅСЏ (РІСЂСѓС‡РЅСѓ)

Р›РѕРіС–РЅ С– РїР°СЂРѕР»СЊ tenis4u Р±РµСЂСѓС‚СЊСЃСЏ Р· СЃРµРєСЂРµС‚С–РІ TENIS4U_LOGIN / TENIS4U_PASSWORD.
Р”Р°РЅС– РїСЂРѕ Р±СЂРѕРЅС– Р·Р±РµСЂС–РіР°СЋС‚СЊСЃСЏ Р»РёС€Рµ РІ data/state.json (РєРµС€ Actions), РЅРµ РІ СЂРµРїРѕР·РёС‚РѕСЂС–С—.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "data" / "state.json"
SEP = " В· "
UA_DAYS = ["РџРЅ", "Р’С‚", "РЎСЂ", "Р§С‚", "РџС‚", "РЎР±", "РќРґ"]
T4U_CLIENT = ("tenis", "secret")  # РїСѓР±Р»С–С‡РЅРёР№ OAuth-РєР»С–С”РЅС‚ РІРµР±Р·Р°СЃС‚РѕСЃСѓРЅРєСѓ tenis4u
MY_RESERVATIONS_URL = "https://app.tenis4u.pl/user/dashboard"


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def to_hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def fmt_day(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{UA_DAYS[d.weekday()]} {d:%d.%m}"


# ---------------------------------------------------------------- Telegram

def tg(method: str, payload: dict, timeout: int = 30):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print(f"[telegram {method}] {json.dumps(payload, ensure_ascii=False)[:500]}")
        return None
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")).get("result")
    except urllib.error.HTTPError as exc:  # Р±РµР· URL: Сѓ РЅСЊРѕРјСѓ С‚РѕРєРµРЅ
        log(f"telegram {method}: HTTP {exc.code} {exc.read()[:200]!r}")
    except Exception as exc:  # noqa: BLE001
        log(f"telegram {method}: {type(exc).__name__}")
    return None


def tg_send(text: str, buttons: list[list[tuple[str, str]]] | None = None) -> int | None:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    payload: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [
            [{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
    res = tg("sendMessage", payload)
    return res.get("message_id") if isinstance(res, dict) else None


def tg_edit(message_id: int | None, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> None:
    if not message_id:
        tg_send(text, buttons)
        return
    payload: dict = {"chat_id": os.environ.get("TELEGRAM_CHAT_ID", "").strip(), "message_id": message_id,
                     "text": text, "disable_web_page_preview": True}
    payload["reply_markup"] = {"inline_keyboard": [
        [{"text": t, "callback_data": d} for t, d in row] for row in (buttons or [])]}
    tg("editMessageText", payload)


def tg_send_file(filename: str, content: bytes, caption: str = "") -> None:
    """РќР°РґСЃРёР»Р°С” С„Р°Р№Р» (sendDocument, multipart). Р‘РµР· С‚РѕРєРµРЅР° Р»РёС€Рµ РґСЂСѓРєСѓС”, С‰Рѕ РЅР°РґС–СЃР»Р°РІ Р±Рё."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token:
        print(f"[telegram file {filename}, {len(content)} Р±Р°Р№С‚] {caption}")
        return
    boundary = "----padelmonitor" + str(int(time.time() * 1000))
    parts = []
    for name, value in (("chat_id", chat_id), ("caption", caption)):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\n'
                 f"Content-Type: text/calendar; charset=utf-8\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendDocument", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=30):
            pass
    except urllib.error.HTTPError as exc:  # Р±РµР· URL: Сѓ РЅСЊРѕРјСѓ С‚РѕРєРµРЅ
        log(f"telegram sendDocument: HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001
        log(f"telegram sendDocument: {type(exc).__name__}")


def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def make_ics(item: dict, src: dict, tz: ZoneInfo, note: str = "") -> bytes:
    """РџРѕРґС–СЏ РєР°Р»РµРЅРґР°СЂСЏ РґР»СЏ Р±СЂРѕРЅС– (С‡Р°СЃ РІ UTC, РЅР°РіР°РґСѓРІР°РЅРЅСЏ Р·Р° 2 РіРѕРґ)."""
    start = datetime.fromisoformat(f"{item['day']}T{to_hm(item['start'])}").replace(tzinfo=tz)
    end = start + timedelta(minutes=item["end"] - item["start"])
    fmt = "%Y%m%dT%H%M%SZ"
    court = item["court"].split(SEP, 1)[-1]
    uid = cal_uid(item)
    desc = "\n".join(x for x in (note, my_url(src)) if x)
    title = f"РџР°РґРµР» В· {src.get('name', item['src'])} В· {court}"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//padel-monitor//UA", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{ics_escape(uid)}",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime(fmt)}",
        f"DTSTART:{start.astimezone(timezone.utc).strftime(fmt)}",
        f"DTEND:{end.astimezone(timezone.utc).strftime(fmt)}",
        f"SUMMARY:{ics_escape(title)}",
        f"LOCATION:{ics_escape(src.get('address') or src.get('name', ''))}",
        f"DESCRIPTION:{ics_escape(desc)}",
        "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:РџР°РґРµР»", "TRIGGER:-PT2H", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR", "",
    ]
    return "\r\n".join(lines).encode("utf-8")


def cal_uid(item: dict) -> str:
    return f"{item.get('uuid') or item.get('key') or item['day']}@padel-monitor"


def send_calendar(item: dict, src: dict, tz: ZoneInfo, note: str = "") -> None:
    """Р”РѕРґР°С” Р±СЂРѕРЅСЊ Сѓ РєР°Р»РµРЅРґР°СЂ iCloud (СЏРєС‰Рѕ С” СЃРµРєСЂРµС‚Рё ICLOUD_*), С–РЅР°РєС€Рµ РЅР°РґСЃРёР»Р°С” С„Р°Р№Р» .ics Сѓ Telegram."""
    ics = make_ics(item, src, tz, note)
    item["cal_uid"] = cal_uid(item)
    try:
        import icloud_cal
        if icloud_cal.configured():
            icloud_cal.put_event(item["cal_uid"], ics)  # РјРѕРІС‡РєРё: РїРёС€РµРјРѕ РІ Telegram Р»РёС€Рµ РїСЂРѕ РїРѕРјРёР»РєРё
            return
    except Exception as exc:  # noqa: BLE001 - РєР°Р»РµРЅРґР°СЂ РЅРµ РјР°С” Р»Р°РјР°С‚Рё Р±СЂРѕРЅСЋРІР°РЅРЅСЏ
        tg_send(f"вљ пёЏ РќРµ РІРґР°Р»РѕСЃСЏ РґРѕРґР°С‚Рё Р±СЂРѕРЅСЊ Сѓ РєР°Р»РµРЅРґР°СЂ iCloud: {exc}")
    fname = f"padel-{item['day']}-{to_hm(item['start']).replace(':', '')}.ics"
    tg_send_file(fname, ics, "рџ“… Р¤Р°Р№Р» РїРѕРґС–С— РґР»СЏ РєР°Р»РµРЅРґР°СЂСЏ")


def remove_calendar(item: dict) -> None:
    try:
        import icloud_cal
        if icloud_cal.configured():
            icloud_cal.delete_event(item.get("cal_uid") or cal_uid(item))
    except Exception as exc:  # noqa: BLE001
        tg_send(f"вљ пёЏ РќРµ РІРґР°Р»РѕСЃСЏ РїСЂРёР±СЂР°С‚Рё РїРѕРґС–СЋ Р· РєР°Р»РµРЅРґР°СЂСЏ iCloud: {exc}. Р’РёРґР°Р»С–С‚СЊ С—С— РІСЂСѓС‡РЅСѓ.")


# ---------------------------------------------------------------- tenis4u API

class T4UError(RuntimeError):
    pass


class Tenis4u:
    def __init__(self, src: dict):
        self.api = src.get("api_base", "https://api.tenis4u.pl").rstrip("/")
        self.header = src["client_header"]
        self.token: str | None = None

    def _req(self, method: str, path: str, body=None, form: str | None = None, auth: str | None = None):
        headers = {"X-Tenis-User-Agent": self.header, "Accept": "application/json",
                   "User-Agent": "padel-slot-monitor/1.0"}
        data = None
        if form is not None:
            data = form.encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = auth
        elif self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.api + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                err = json.loads(raw)
                msg = err.get("error") or err.get("message") or raw
                if isinstance(err.get("message"), str) and err.get("message") != msg:
                    msg = f"{msg}: {err['message']}"
            except json.JSONDecodeError:
                msg = raw[:200]
            raise T4UError(f"HTTP {exc.code} {method} {path.split('?')[0]}: {msg}") from None

    def login(self) -> None:
        user = os.environ.get("TENIS4U_LOGIN", "").strip()
        pw = os.environ.get("TENIS4U_PASSWORD", "")
        if not user or not pw:
            raise T4UError("РЅРµРјР°С” СЃРµРєСЂРµС‚С–РІ TENIS4U_LOGIN / TENIS4U_PASSWORD")
        basic = base64.b64encode(f"{T4U_CLIENT[0]}:{T4U_CLIENT[1]}".encode()).decode()
        form = "grant_type=password&username=" + urllib.parse.quote(user) + "&password=" + urllib.parse.quote(pw)
        res = self._req("POST", "/oauth/token", form=form, auth=f"Basic {basic}")
        self.token = res.get("access_token")
        if not self.token:
            raise T4UError("РІС…С–Рґ РЅРµ РІРґР°РІСЃСЏ (РЅРµРјР°С” access_token)")

    def my_reservations(self) -> list[tuple[str, dict]]:
        """[(endpoint, Р±СЂРѕРЅСЊ)] Р· РѕР±РѕС… СЃРїРёСЃРєС–РІ Р±СЂРѕРЅРµР№ tenis4u (РЅРѕРІРѕРіРѕ v3 С– СЃС‚Р°СЂРѕРіРѕ)."""
        out: list[tuple[str, dict]] = []
        for path in ("/api/v3/reservations/my", "/reservation/list"):
            try:
                res = self._req("GET", path)
            except T4UError as exc:
                log(f"tenis4u {path}: {exc}")
                continue
            items = res if isinstance(res, list) else next(
                (v for v in (res or {}).values() if isinstance(v, list)), [])
            out += [(path, it) for it in items if isinstance(it, dict)]
        return out

    def my_days(self) -> set[str]:
        """Р”РЅС–, РЅР° СЏРєС– Сѓ РІР°СЃ СѓР¶Рµ С” Р°РєС‚РёРІРЅР° Р±СЂРѕРЅСЊ Сѓ tenis4u (Р±СѓРґСЊ-СЏРєРёР№ РєР»СѓР±)."""
        days: set[str] = set()
        for _path, it in self.my_reservations():
            status = str(it.get("status") or it.get("reservation_status") or "").upper()
            if any(s in status for s in ("CANCEL", "DELETED", "REJECT")):
                continue
            for k in ("start_time", "starts_at", "startTime", "start_date", "date", "day"):
                v = it.get(k)
                if isinstance(v, str) and len(v) >= 10:
                    days.add(v[:10].replace("/", "-"))
                    break
        return days

    def tariff_id(self, station_id: int, start: datetime, end: datetime) -> tuple[int | None, float | None, list[str]]:
        q = urllib.parse.urlencode({
            "lightning": "false", "multisport": 0, "medicover": 0,
            "start": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "end": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        })
        res = self._req("GET", f"/price/tariff/calculate/{station_id}?{q}")
        items = res.get("value", []) if isinstance(res, dict) and "value" in res else [res]
        items = [t for t in items if isinstance(t, dict) and t.get("id")]
        cash = [t for t in items if "CASH" in (t.get("available_payment_methods") or [])]
        best = (cash or items or [None])[0]
        return (best or {}).get("id"), (best or {}).get("cost"), list((best or {}).get("available_payment_methods") or [])

    def book(self, station_id: int, start: datetime, end: datetime) -> dict:
        tariff, cost, methods = self.tariff_id(station_id, start, end)
        if methods and "CASH" not in methods:
            raise T4UError("РєР»СѓР± РїСЂРёР№РјР°С” Р·Р° С†РµР№ С‡Р°СЃ Р»РёС€Рµ РѕРЅР»Р°Р№РЅ-РѕРїР»Р°С‚Сѓ вЂ” РЅРµ Р±СЂРѕРЅСЋСЋ")
        body = {
            "station_id": int(station_id),
            "start_time": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "declared_player_count": 1,
            "payment_strategy": "ORGANIZER_PAYS",
            "lightning": False,
            "tariff_id": tariff,
        }
        if "CASH" in methods or not methods:
            # Р·Р°РІР¶РґРё СЏРІРЅРѕ РѕР±РёСЂР°С”РјРѕ РіРѕС‚С–РІРєСѓ (СЏРє РєРЅРѕРїРєР° В«GotГіwkaВ» РІ Р·Р°СЃС‚РѕСЃСѓРЅРєСѓ): Р±РµР· С†СЊРѕРіРѕ Padel House
            # СЃС‚Р°РІРёС‚СЊ РѕРЅР»Р°Р№РЅ-РѕРїР»Р°С‚Сѓ (СЃРєР°СЃСѓРІР°РЅРЅСЏ С‡РµСЂРµР· 30 С…РІ), Р° Fame Р»РёС€Р°С” Р±СЂРѕРЅСЊ В«РґРѕ РѕРїР»Р°С‚РёВ»
            body["payment"] = {"payment_method": "CASH",
                               "payment_config": {"return_url": "https://app.tenis4u.pl/user/dashboard"}}
        res = self._req("POST", "/api/v3/reservations", body=body)
        res = res if isinstance(res, dict) else {}
        pay = res.get("payment") if isinstance(res.get("payment"), dict) else {}
        if pay.get("redirect_url") or str(pay.get("payment_method") or pay.get("method") or "").upper() == "ONLINE":
            uuid = res.get("public_uuid") or res.get("uuid") or res.get("id")
            if uuid:
                try:
                    self.cancel(str(uuid))
                except T4UError as exc:
                    log(f"РЅРµ РІРґР°Р»РѕСЃСЏ СЃРєР°СЃСѓРІР°С‚Рё РѕРЅР»Р°Р№РЅ-Р±СЂРѕРЅСЊ {uuid}: {exc}")
            raise T4UError("РєР»СѓР± РѕС„РѕСЂРјРёРІ Р±СЂРѕРЅСЊ Р· РѕРЅР»Р°Р№РЅ-РѕРїР»Р°С‚РѕСЋ (СЃР°РјР° СЃРєР°СЃСѓРІР°Р»Р°СЃСЊ Р±Рё Р·Р° 30 С…РІ) вЂ” СЃРєР°СЃСѓРІР°РІ С—С—")
        res["_cost"] = cost
        return res

    def pay_cash(self, uuid: str) -> dict:
        """РћР±СЂР°С‚Рё РѕРїР»Р°С‚Сѓ РіРѕС‚С–РІРєРѕСЋ РґР»СЏ РІР¶Рµ СЃС‚РІРѕСЂРµРЅРѕС— Р±СЂРѕРЅС– (СЏРє В«РћРїР»Р°С‚РёС‚Рё -> GotГіwkaВ» РІ Р·Р°СЃС‚РѕСЃСѓРЅРєСѓ)."""
        return self._req("POST", f"/api/v3/reservations/{uuid}/payments", body={
            "payment_method": "CASH",
            "payment_config": {"return_url": f"https://app.tenis4u.pl/reservation/{uuid}"}})

    def cancel(self, uuid: str) -> None:
        self._req("DELETE", f"/api/v3/reservations/{uuid}")


# ---------------------------------------------------------------- twojtenis.pl API

class Twojtenis:
    """Р‘СЂРѕРЅСЋРІР°РЅРЅСЏ РІ РєР»СѓР±С– РЅР° twojtenis.pl. Р’С…С–Рґ С‡РµСЂРµР· РЅРµРІРёРґРёРјРёР№ Р±СЂР°СѓР·РµСЂ (tt_login.py)."""

    def __init__(self, src: dict):
        self.src = src
        self.api = src["api_base"].rstrip("/")            # .../api/v1
        self.club = src["club_id"]
        self.token: str | None = None
        self.svc: str | None = None                        # https://...tech-krakow...

    def _req(self, method: str, url: str, body=None):
        headers = {"Accept": "application/json", "User-Agent": "padel-slot-monitor/1.0"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                err = json.loads(raw)
                msg = err.get("error") or err.get("title") or err.get("message") or raw[:200]
                if isinstance(err, dict) and err.get("errors"):  # ASP.NET: {"errors": {"РїРѕР»Рµ": ["РїСЂРёС‡РёРЅР°"]}}
                    msg = f"{msg} {json.dumps(err['errors'], ensure_ascii=False)[:400]}"
                elif isinstance(err, dict) and err.get("detail"):
                    msg = f"{msg}: {err['detail']}"
                elif msg in ("Bad Request", "Conflict"):
                    msg = f"{msg} {raw[:300]}"
            except json.JSONDecodeError:
                msg = raw[:200]
            path = urllib.parse.urlparse(url).path
            raise T4UError(f"HTTP {exc.code} {method} {path}: {msg}") from None

    def login(self) -> None:
        import tt_login
        try:
            self.token, _ = tt_login.get_token()
        except tt_login.LoginError as exc:
            raise T4UError(f"РІС…С–Рґ Сѓ twojtenis: {exc}") from None
        self.svc = self._req("GET", f"{self.api}/Clubs/{self.club}/technical-group")["serviceUrl"].rstrip("/")

    def my_bookings(self) -> list[dict]:
        today = date.today()
        q = urllib.parse.urlencode({"from": today.isoformat(), "to": (today + timedelta(days=40)).isoformat()})
        items = self._req("GET", f"{self.svc}/api/v1/bookings/my?{q}") or []
        return [b for b in items if isinstance(b, dict) and not b.get("isDeleted")]

    def my_days(self) -> set[str]:
        return {str(b.get("date"))[:10] for b in self.my_bookings()}

    def book(self, location_id: str, location_name: str, day: str, start: str, end: str) -> dict:
        prof, price = self._prepare(location_id, day, start, end)
        body = self._body(prof, price, location_id, location_name, day, start, end)
        res = self._req("POST", f"{self.svc}/api/v1/Clubs/{self.club}/bookings", body)
        first = res[0] if isinstance(res, list) and res else res if isinstance(res, dict) else {}
        first["_cost"] = price["price"]
        return first

    def probe(self, location_id: str, location_name: str) -> str:
        """Р”С–Р°РіРЅРѕСЃС‚РёРєР°: СЃРїСЂР°РІР¶РЅСЏ С†С–РЅР° РЅР° РјР°Р№Р±СѓС‚РЅС–Р№ РґРµРЅСЊ, Р°Р»Рµ РґР°С‚Р° Р±СЂРѕРЅС– вЂ” РІС‡РѕСЂР°.
        РЎРµСЂРІРµСЂ СЃРїРµСЂС€Сѓ РїРµСЂРµРІС–СЂСЏС” С„РѕСЂРјР°С‚ РїРѕР»С–РІ; Р±СЂРѕРЅСЊ Сѓ РјРёРЅСѓР»РѕРјСѓ СЃС‚РІРѕСЂРёС‚Рё РЅРµРјРѕР¶Р»РёРІРѕ."""
        future = (date.today() + timedelta(days=3)).isoformat()
        past = (date.today() - timedelta(days=1)).isoformat()
        prof, price = self._prepare(location_id, future, "21:00:00", "22:30:00")
        body = self._body(prof, price, location_id, location_name, past, "21:00:00", "22:30:00")
        nulls = [k for k, v in body.items() if v is None] + [f"requests.{k}" for k, v in body["requests"][0].items() if v is None]
        try:
            res = self._req("POST", f"{self.svc}/api/v1/Clubs/{self.club}/bookings", body)
            return f"РќР•РћР§Р†РљРЈР’РђРќРћ РїСЂРёР№РЅСЏС‚Рѕ: {json.dumps(res, ensure_ascii=False)[:300]}"
        except T4UError as exc:
            return f"{exc} | РїРѕСЂРѕР¶РЅС– РїРѕР»СЏ: {nulls or '-'}"

    def _prepare(self, location_id: str, day: str, start: str, end: str) -> tuple[dict, dict]:
        me = self._req("GET", f"{self.api}/Players/me")
        try:
            prof = self._req("GET", f"{self.api}/Clubs/{self.club}/players/{me['id']}")
        except T4UError as exc:
            if "HTTP 404" not in str(exc):
                raise
            prof = self._req("POST", f"{self.api}/Clubs/{self.club}/players/{me['id']}")
        if prof.get("isBlocked"):
            raise T4UError("РІР°С€ РїСЂРѕС„С–Р»СЊ Сѓ РєР»СѓР±С– Р·Р°Р±Р»РѕРєРѕРІР°РЅРѕ")
        calc = self._req("POST", f"{self.api}/Clubs/{self.club}/actions/calculate-price", {
            "clubId": self.club, "locationId": location_id, "start": start, "end": end, "days": [day],
            "multiSportCardsUsed": 0, "medicoverCardsUsed": 0, "playerId": prof["id"],
            "isSmartTennisMatch": False, "isOnlinePayment": False})
        price = (calc.get("prices") or [{}])[0]
        if not price.get("checksum") or price.get("failed"):
            raise T4UError(f"РєР»СѓР± РЅРµ РґР°РІ С†С–РЅСѓ РЅР° С†РµР№ С‡Р°СЃ: {json.dumps(calc, ensure_ascii=False)[:200]}")
        return prof, price

    def _body(self, prof: dict, price: dict, location_id: str, location_name: str,
              day: str, start: str, end: str) -> dict:
        due = datetime.fromisoformat(f"{day}T{start}").replace(tzinfo=ZoneInfo("Europe/Warsaw"))
        return {
            "requests": [{
                "payment": {"amountToPay": price["price"], "discountType": "absolute", "discountValue": 0,
                            "paidAmount": 0, "initialAmount": price["price"],
                            "paymentDue": due.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                            "status": "awaiting", "isOnline": False},
                "locationId": location_id, "startHour": start, "endHour": end, "date": day,
                "price": price["price"], "checksum": price["checksum"], "locationName": location_name,
                "multiSportCardsUsed": price.get("multiSportCardsUsed", 0),
                "medicoverCardsUsed": price.get("medicoverCardsUsed", 0),
            }],
            "description": "", "bookerId": prof["id"],
            "bookerName": f"{prof.get('firstName', '')} {prof.get('lastName', '')}".strip(),
            # "comment" РЅРµ РїРµСЂРµРґР°С”РјРѕ: СЃРµСЂРІРµСЂ С‡РµРєР°С” С‚Р°Рј РѕР±'С”РєС‚ РєРѕРјРµРЅС‚Р°СЂСЏ, Р° Р·Р°СЃС‚РѕСЃСѓРЅРѕРє Р±РµР· РєРѕРјРµРЅС‚Р°СЂСЏ РїРѕР»Рµ РїСЂРѕРїСѓСЃРєР°С”
            "bookerType": 0, "clubName": self.src.get("name", ""),
            "bookerEmail": prof.get("email"), "bookerPhone": prof.get("phoneNumber"),
            "source": 0, "isSmartTennisMatch": False, "isOnlinePayment": False, "webApp": True,
        }

    def cancel(self, booking_id: str) -> None:
        self._req("POST", f"{self.svc}/api/v1/Bookings/my/{booking_id}/cancel", {})


def client_for(src: dict):
    return Twojtenis(src) if src.get("type") == "twojtenis" else Tenis4u(src)


CRED_ENV = {"tenis4u": "TENIS4U_LOGIN", "twojtenis": "TWOJTENIS_LOGIN"}


# ---------------------------------------------------------------- decisions

def bstate(state: dict) -> dict:
    b = state.setdefault("booking", {})
    b.setdefault("bookings", [])   # {id, day, start, end, court, src, uuid, auto, msg}
    b.setdefault("offers", {})     # id -> {day, start, end, court, src, msg, status}
    b.setdefault("skipped_days", [])
    b.setdefault("failed", [])     # РєР»СЋС‡С– СЃРїСЂРѕР±, С‰Рѕ РЅРµ РІРґР°Р»РёСЃСЏ (РЅРµ РїРѕРІС‚РѕСЂСЋС”РјРѕ)
    b.setdefault("seq", 0)
    return b


def next_id(b: dict) -> str:
    b["seq"] += 1
    return str(b["seq"])


def active_bookings(b: dict, now: datetime) -> list[dict]:
    return [x for x in b["bookings"] if x.get("status") == "active" and x["day"] >= now.date().isoformat()]


def my_url(src: dict) -> str:
    return src.get("booking_url", "") if src.get("type") in ("twojtenis", "ptmates") else MY_RESERVATIONS_URL


def plan_for_block(block: dict, bcfg: dict) -> tuple[int, int]:
    length = block["end"] - block["start"]
    dur = bcfg.get("prefer_minutes", 120) if length >= bcfg.get("prefer_minutes", 120) else bcfg.get("min_minutes", 90)
    return block["start"], block["start"] + dur


def describe(item: dict, sources: dict) -> str:
    src = sources.get(item["src"], {})
    court = item["court"].split(SEP, 1)[-1]
    return (f"{src.get('name', item['src'])} В· {court}\n"
            f"{fmt_day(item['day'])} {to_hm(item['start'])}вЂ“{to_hm(item['end'])}")


def cancel_deadline(item: dict, tz: ZoneInfo, hours: int) -> datetime:
    start = datetime.fromisoformat(f"{item['day']}T{to_hm(item['start'])}").replace(tzinfo=tz)
    return start - timedelta(hours=hours)


def do_book(item: dict, sources: dict, court_ids: dict, tz: ZoneInfo) -> tuple[bool, str]:
    """РџРѕРІРµСЂС‚Р°С” (СѓСЃРїС–С…, С‚РµРєСЃС‚)."""
    src = sources[item["src"]]
    station = court_ids.get(item["court"])
    if not station:
        return False, "РЅРµ Р·РЅР°СЋ id РєРѕСЂС‚Сѓ (Р·Р°С‡РµРєР°Р№С‚Рµ РЅР°СЃС‚СѓРїРЅРѕС— РїРµСЂРµРІС–СЂРєРё)"
    start = datetime.fromisoformat(f"{item['day']}T{to_hm(item['start'])}").replace(tzinfo=tz)
    end = datetime.fromisoformat(f"{item['day']}T{to_hm(item['end'] % (24 * 60))}").replace(tzinfo=tz)
    if item["end"] >= 24 * 60:
        end += timedelta(days=1)
    client = client_for(src)
    try:
        client.login()
        if item["day"] in client.my_days():
            return False, f"РЅР° С†РµР№ РґРµРЅСЊ Сѓ РІР°СЃ СѓР¶Рµ С” Р±СЂРѕРЅСЊ Сѓ {src.get('name', item['src'])}"
        if isinstance(client, Twojtenis):
            res = client.book(station, item["court"].split(SEP, 1)[-1], item["day"],
                              f"{to_hm(item['start'])}:00", f"{to_hm(item['end'] % (24 * 60))}:00")
        else:
            res = client.book(station, start, end)
    except T4UError as exc:
        return False, str(exc)
    uuid = res.get("public_uuid") or res.get("uuid") or res.get("id")
    if not uuid:
        return False, f"РЅРµР·СЂРѕР·СѓРјС–Р»Р° РІС–РґРїРѕРІС–РґСЊ СЃРµСЂРІРµСЂР°: {json.dumps(res, ensure_ascii=False)[:300]}"
    item["uuid"] = str(uuid)
    item["cost"] = res.get("_cost")
    item["status"] = "active"
    if res.get("cancelUntil"):
        item["cancel_until"] = str(res["cancelUntil"])
    extra = ""
    status = str(res.get("status") or "")
    if status and status.upper() not in ("CONFIRMED", "ACTIVE", "CREATED"):
        extra = f"\nРЎС‚Р°С‚СѓСЃ Р±СЂРѕРЅСЋРІР°РЅРЅСЏ: {status} вЂ” РїРµСЂРµРІС–СЂС‚Рµ РІ Р·Р°СЃС‚РѕСЃСѓРЅРєСѓ."
    return True, extra


def needs_cash(it: dict) -> bool:
    """Р‘СЂРѕРЅСЊ tenis4u, РґРµ РІРё РѕСЂРіР°РЅС–Р·Р°С‚РѕСЂ С– СЃРїРѕСЃС–Р± РѕРїР»Р°С‚Рё С‰Рµ РЅРµ РѕР±СЂР°РЅРѕ (СЃС‚Р°С‚СѓСЃ PENDING_PAYMENT / РѕРїР»Р°С‚Р° NONE)."""
    pay = str(it.get("my_payment_status") or "").upper()
    status = str(it.get("status") or "").upper()
    role = str(it.get("my_role") or "ORGANIZER").upper()
    return role == "ORGANIZER" and (pay in ("", "NONE", "UNPAID", "PENDING_PAYMENT") and
                                    (status == "PENDING_PAYMENT" or pay in ("UNPAID", "PENDING_PAYMENT")))


def verify_bookings(b: dict, sources: dict, now: datetime) -> None:
    """Р Р°Р· РЅР° РіРѕРґРёРЅСѓ Р·РІС–СЂСЏС” Р°РєС‚РёРІРЅС– Р±СЂРѕРЅС– Р±РѕС‚Р° РІ tenis4u: СЃРєР°СЃРѕРІР°РЅС– (РєР»СѓР±РѕРј С‡Рё РІР°РјРё) Р·РЅС–РјР°С”,
    С‰РѕР± РґРµРЅСЊ Р·РЅРѕРІСѓ РјРѕР¶РЅР° Р±СѓР»Рѕ Р±СЂРѕРЅСЋРІР°С‚Рё, С– РїСЂРёР±РёСЂР°С” РїРѕРґС–СЋ Р· РєР°Р»РµРЅРґР°СЂСЏ."""
    if time.time() - b.get("verified_at", 0) < 3600:
        return
    mine = [x for x in active_bookings(b, now) if (sources.get(x["src"]) or {}).get("type") == "tenis4u"]
    if not mine:
        b["verified_at"] = time.time()
        return
    try:
        client = Tenis4u(sources[mine[0]["src"]])
        client.login()
        alive = set()
        unpaid = set()
        for _p, it in client.my_reservations():
            status = str(it.get("status") or "").upper()
            if not any(s in status for s in ("CANCEL", "DELETED", "REJECT", "EXPIRED")):
                uid = str(it.get("public_uuid") or it.get("uuid") or it.get("id"))
                alive.add(uid)
                if needs_cash(it):
                    unpaid.add(uid)
        for x in mine:  # Р±СЂРѕРЅС– Р±РѕС‚Р° В«РґРѕ РѕРїР»Р°С‚РёВ» -> РѕРїР»Р°С‚Р° РіРѕС‚С–РІРєРѕСЋ
            if x.get("uuid") in unpaid:
                try:
                    res = client.pay_cash(x["uuid"])
                    if isinstance(res, dict) and (res.get("payment") or {}).get("redirect_url"):
                        log(f"pay_cash {x['uuid']}: РєР»СѓР± РїРѕРІРµСЂРЅСѓРІ РѕРЅР»Р°Р№РЅ-РѕРїР»Р°С‚Сѓ")
                    else:
                        log(f"pay_cash {x['uuid']}: РіРѕС‚С–РІРєР°")
                except T4UError as exc:
                    log(f"pay_cash {x['uuid']}: {exc}")
    except T4UError as exc:
        log(f"verify: {exc}")
        return
    b["verified_at"] = time.time()
    for x in mine:
        if x.get("uuid") and x["uuid"] not in alive:
            x["status"] = "gone"
            remove_calendar(x)
            tg_send(f"в„№пёЏ Р‘СЂРѕРЅСЊ Р±С–Р»СЊС€Рµ РЅРµ Р°РєС‚РёРІРЅР° (СЃРєР°СЃРѕРІР°РЅР° РєР»СѓР±РѕРј Р°Р±Рѕ РІР°РјРё), РїСЂРёР±СЂР°РІ Р· РєР°Р»РµРЅРґР°СЂСЏ:\n"
                    f"{describe(x, sources)}")


def cancel_text(item: dict, tz: ZoneInfo, hours: int) -> str:
    """В«РЎРєР°СЃСѓРІР°С‚Рё Р±РµР·РєРѕС€С‚РѕРІРЅРѕ РјРѕР¶РЅР° РґРѕ вЂ¦В» вЂ” Р· РІС–РґРїРѕРІС–РґС– РєР»СѓР±Сѓ (twojtenis) Р°Р±Рѕ Р·Р° РЅР°Р»Р°С€С‚СѓРІР°РЅРЅСЏРј."""
    dl = None
    if item.get("cancel_until"):
        try:
            dl = datetime.fromisoformat(item["cancel_until"].replace("Z", "+00:00"))
            dl = dl.replace(tzinfo=tz) if dl.tzinfo is None else dl.astimezone(tz)
        except ValueError:
            dl = None
    dl = dl or cancel_deadline(item, tz, hours)
    return f"РЎРєР°СЃСѓРІР°С‚Рё Р±РµР·РєРѕС€С‚РѕРІРЅРѕ РјРѕР¶РЅР° РґРѕ {fmt_day(dl.date().isoformat())} {dl:%H:%M}."


def process(blocks: list[dict], cfg: dict, state: dict, now: datetime, tz: ZoneInfo,
            sources: dict, court_ids: dict) -> set[str]:
    """Р’РёРєР»РёРєР°С”С‚СЊСЃСЏ Р· collector.py РїС–СЃР»СЏ РєРѕР¶РЅРѕС— РїРµСЂРµРІС–СЂРєРё. РџРѕРІРµСЂС‚Р°С” РґРЅС–, РїСЂРѕ СЏРєС– РЅРµ С‚СЂРµР±Р°
    РЅР°РґСЃРёР»Р°С‚Рё Р·РІРёС‡Р°Р№РЅС– СЃРїРѕРІС–С‰РµРЅРЅСЏ (С‚Р°Рј СѓР¶Рµ С” Р±СЂРѕРЅСЊ Р°Р±Рѕ РїСЂРѕРїРѕР·РёС†С–СЏ)."""
    bcfg = cfg.get("booking") or {}
    if not bcfg.get("enabled"):
        return set()
    b = bstate(state)
    allowed = set()
    for short in bcfg.get("sources", []):
        env = CRED_ENV.get((sources.get(short) or {}).get("type", ""), "")
        if env and os.environ.get(env):
            allowed.add(short)
        elif env and not b.get(f"warned_{env}"):
            b[f"warned_{env}"] = True
            tg_send(f"вљ пёЏ РђРІС‚РѕР±СЂРѕРЅСЋРІР°РЅРЅСЏ РґР»СЏ {short} СѓРІС–РјРєРЅРµРЅРµ, Р°Р»Рµ РЅРµРјР°С” СЃРµРєСЂРµС‚Сѓ {env} Сѓ GitHub.")
    if not allowed:
        return set()

    today = now.date().isoformat()
    if not os.environ.get("PADEL_SELFTEST"):
        verify_bookings(b, sources, now)
    booked_days = {x["day"] for x in active_bookings(b, now)}
    # РїСЂРёР±РёСЂР°С”РјРѕ СЃС‚Р°СЂРµ
    b["offers"] = {k: o for k, o in b["offers"].items() if o["day"] >= today}
    b["skipped_days"] = [d for d in b["skipped_days"] if d >= today]
    b["failed"] = [k for k in b["failed"] if k[:10] >= today]
    b["bookings"] = [x for x in b["bookings"] if x["day"] >= (now.date() - timedelta(days=7)).isoformat()]

    block_keys = {(x["day"], x["court"], x["start"]) for x in blocks}
    open_offer_days = set()
    for oid, o in b["offers"].items():
        if o.get("status") != "open":
            continue
        still_free = any(bl["day"] == o["day"] and bl["court"] == o["court"]
                         and bl["start"] <= o["start"] and bl["end"] >= o["end"] for bl in blocks)
        if still_free:
            open_offer_days.add(o["day"])
        else:
            o["status"] = "gone"
            tg_edit(o.get("msg"), "вЊ› РЎР»РѕС‚ СѓР¶Рµ Р·Р°Р№РЅСЏР»Рё:\n" + describe(o, sources))

    quiet = booked_days | open_offer_days | set(b["skipped_days"])
    # РєР°РЅРґРёРґР°С‚Рё: РЅР°Р№СЂР°РЅС–С€РёР№ Р±Р»РѕРє Сѓ РєРѕР¶РµРЅ РґРµРЅСЊ
    by_day: dict[str, dict] = {}
    for bl in sorted(blocks, key=lambda x: (x["day"], x["start"], x["court"])):
        src = bl["court"].split(SEP, 1)[0]
        if src not in allowed or bl["day"] in quiet or bl["day"] in by_day:
            continue
        start, end = plan_for_block(bl, bcfg)
        key = f"{bl['day']}|{start}|{bl['court']}"
        if key in b["failed"]:
            continue
        by_day[bl["day"]] = {"day": bl["day"], "start": start, "end": end, "court": bl["court"], "src": src,
                             "key": key}

    auto_hours = bcfg.get("auto_min_lead_hours", 30)
    max_auto = bcfg.get("max_active_auto", 3)
    cancel_h = bcfg.get("cancel_limit_hours", 24)
    for day, item in sorted(by_day.items()):
        start_dt = datetime.fromisoformat(f"{day}T{to_hm(item['start'])}").replace(tzinfo=tz)
        lead_h = (start_dt - now).total_seconds() / 3600
        n_auto = sum(1 for x in active_bookings(b, now) if x.get("auto"))
        if lead_h >= auto_hours and n_auto < max_auto:
            item["id"] = next_id(b)
            item["auto"] = True
            ok, extra = do_book(item, sources, court_ids, tz)
            if ok:
                b["bookings"].append(item)
                quiet.add(day)
                cost = f"\nР¦С–РЅР°: {item['cost']:g} zЕ‚ (РѕРїР»Р°С‚Р° РІ РєР»СѓР±С–)" if item.get("cost") else ""
                item["msg"] = tg_send(
                    f"вњ… Р—Р°Р±СЂРѕРЅСЋРІР°РІ РєРѕСЂС‚\n{describe(item, sources)}{cost}\n"
                    f"{cancel_text(item, tz, cancel_h)}{extra}\n"
                    f"{my_url(sources[item['src']])}",
                    [[("вќЊ РЎРєР°СЃСѓРІР°С‚Рё Р±СЂРѕРЅСЊ", f"c:{item['id']}")]])
                send_calendar(item, sources[item["src"]], tz, f"{cost.strip()}\n{cancel_text(item, tz, cancel_h)}")
            else:
                b["failed"].append(item["key"])
                tg_send(f"вљ пёЏ РќРµ РІРґР°Р»РѕСЃСЏ Р·Р°Р±СЂРѕРЅСЋРІР°С‚Рё\n{describe(item, sources)}\nРџСЂРёС‡РёРЅР°: {extra}")
            continue
        # РјР°Р»Рѕ С‡Р°СЃСѓ РґРѕ РіСЂРё (Р°Р±Рѕ РґРѕСЃСЏРіРЅСѓС‚Рѕ Р»С–РјС–С‚Сѓ Р°РІС‚РѕР±СЂРѕРЅРµР№) -> РїРёС‚Р°С”РјРѕ
        oid = next_id(b)
        item["status"] = "open"
        why = "Р”Рѕ РіСЂРё РјРµРЅС€Рµ РЅС–Р¶ " + f"{auto_hours} РіРѕРґ" if lead_h < auto_hours else f"РЈР¶Рµ С” {n_auto} Р°РІС‚РѕР±СЂРѕРЅС–"
        item["msg"] = tg_send(
            f"рџЋѕ Р’С–Р»СЊРЅРёР№ РєРѕСЂС‚. Р—Р°Р±СЂРѕРЅСЋРІР°С‚Рё?\n{describe(item, sources)}\n"
            f"({why}, С‚РѕРјСѓ РїРёС‚Р°СЋ. РЎРєР°СЃСѓРІР°С‚Рё РїС–Р·РЅС–С€Рµ РЅС–Р¶ Р·Р° {cancel_h} РіРѕРґ РґРѕ РіСЂРё РјРѕР¶Рµ Р±СѓС‚Рё РЅРµРјРѕР¶Р»РёРІРѕ.)",
            [[("вњ… Р—Р°Р±СЂРѕРЅСЋРІР°С‚Рё", f"b:{oid}"), ("РџСЂРѕРїСѓСЃС‚РёС‚Рё", f"s:{oid}")]])
        b["offers"][oid] = item
        quiet.add(day)
    return quiet


# ---------------------------------------------------------------- Telegram buttons

def handle_callback(cq: dict, state: dict, cfg: dict, tz: ZoneInfo) -> None:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    msg = cq.get("message") or {}
    if str((msg.get("chat") or {}).get("id")) != chat_id:
        return
    tg("answerCallbackQuery", {"callback_query_id": cq["id"]})
    data = str(cq.get("data") or "")
    kind, _, oid = data.partition(":")
    b = bstate(state)
    sources = {s["short"]: s for s in cfg["sources"]}
    court_ids = state.get("court_ids", {})
    now = datetime.now(tz)
    mid = msg.get("message_id")

    if kind in ("b", "s"):
        o = b["offers"].get(oid)
        if not o or o.get("status") != "open":
            tg_edit(mid, (msg.get("text") or "") + "\n\n(РїСЂРѕРїРѕР·РёС†С–СЏ РІР¶Рµ РЅРµР°РєС‚СѓР°Р»СЊРЅР°)")
            return
        if kind == "s":
            o["status"] = "skipped"
            b["skipped_days"].append(o["day"])
            tg_edit(mid, "РџСЂРѕРїСѓС‰РµРЅРѕ:\n" + describe(o, sources))
            return
        if any(x["day"] == o["day"] for x in active_bookings(b, now)):
            o["status"] = "gone"
            tg_edit(mid, "РќР° С†РµР№ РґРµРЅСЊ СѓР¶Рµ С” Р±СЂРѕРЅСЊ:\n" + describe(o, sources))
            return
        o["id"] = oid
        o["auto"] = False
        ok, extra = do_book(o, sources, court_ids, tz)
        if ok:
            o["status"] = "booked"
            b["bookings"].append(dict(o, status="active", msg=mid))
            cost = f"\nР¦С–РЅР°: {o['cost']:g} zЕ‚ (РѕРїР»Р°С‚Р° РІ РєР»СѓР±С–)" if o.get("cost") else ""
            tg_edit(mid, f"вњ… Р—Р°Р±СЂРѕРЅСЊРѕРІР°РЅРѕ\n{describe(o, sources)}{cost}\n"
                         f"{cancel_text(o, tz, (cfg.get('booking') or {}).get('cancel_limit_hours', 24))}"
                         f"{extra}\n{my_url(sources[o['src']])}",
                    [[("вќЊ РЎРєР°СЃСѓРІР°С‚Рё Р±СЂРѕРЅСЊ", f"c:{oid}")]])
            send_calendar(o, sources[o["src"]], tz, f"{cost.strip()}\n"
                          f"{cancel_text(o, tz, (cfg.get('booking') or {}).get('cancel_limit_hours', 24))}")
        else:
            o["status"] = "failed"
            tg_edit(mid, f"вљ пёЏ РќРµ РІРґР°Р»РѕСЃСЏ Р·Р°Р±СЂРѕРЅСЋРІР°С‚Рё\n{describe(o, sources)}\nРџСЂРёС‡РёРЅР°: {extra}")
        return

    if kind == "c":
        x = next((x for x in b["bookings"] if x.get("id") == oid and x.get("status") == "active"), None)
        if not x:
            tg_edit(mid, (msg.get("text") or "") + "\n\n(Р±СЂРѕРЅСЊ РЅРµ Р·РЅР°Р№РґРµРЅРѕ Р°Р±Рѕ РІР¶Рµ СЃРєР°СЃРѕРІР°РЅРѕ)")
            return
        try:
            client = client_for(sources[x["src"]])
            client.login()
            client.cancel(x["uuid"])
        except T4UError as exc:
            tg_send(f"вљ пёЏ РќРµ РІРґР°Р»РѕСЃСЏ СЃРєР°СЃСѓРІР°С‚Рё Р±СЂРѕРЅСЊ\n{describe(x, sources)}\nРџСЂРёС‡РёРЅР°: {exc}\n"
                    f"РЎРєР°СЃСѓР№С‚Рµ РІ Р·Р°СЃС‚РѕСЃСѓРЅРєСѓ: {my_url(sources[x['src']])}")
            return
        x["status"] = "cancelled"
        b["skipped_days"].append(x["day"])  # РЅРµ Р±СЂРѕРЅСЋРІР°С‚Рё С†РµР№ РґРµРЅСЊ Р·РЅРѕРІСѓ Р°РІС‚РѕРјР°С‚РёС‡РЅРѕ
        tg_edit(mid, "рџ—‘ Р‘СЂРѕРЅСЊ СЃРєР°СЃРѕРІР°РЅРѕ\n" + describe(x, sources))
        remove_calendar(x)


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def poll(seconds: int) -> None:
    """РЎР»СѓС…Р°С” РЅР°С‚РёСЃРєР°РЅРЅСЏ РєРЅРѕРїРѕРє seconds СЃРµРєСѓРЅРґ (long polling Telegram)."""
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg["timezone"])
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not (cfg.get("booking") or {}).get("enabled"):
        time.sleep(max(seconds, 0))
        return
    end = time.time() + seconds
    while True:
        left = int(end - time.time())
        if left <= 0:
            return
        state = load_state()
        offset = state.get("tg_offset", 0)
        updates = tg("getUpdates", {"offset": offset, "timeout": min(left, 50),
                                    "allowed_updates": ["callback_query"]}, timeout=min(left, 50) + 15)
        if updates is None:
            time.sleep(min(10, max(end - time.time(), 0)))
            continue
        if not updates:
            continue
        state = load_state()
        for u in updates:
            state["tg_offset"] = u["update_id"] + 1
            if u.get("callback_query"):
                try:
                    handle_callback(u["callback_query"], state, cfg, tz)
                except Exception as exc:  # noqa: BLE001
                    log(f"callback: {type(exc).__name__}: {exc}")
        save_state(state)


def check() -> int:
    """Р СѓС‡РЅР° РїРµСЂРµРІС–СЂРєР°: РІС…С–Рґ С– РґРЅС– Р· РІР°С€РёРјРё Р±СЂРѕРЅСЏРјРё РІ РєРѕР¶РЅРѕРјСѓ РєР»СѓР±С–. РќС–С‡РѕРіРѕ РЅРµ Р±СЂРѕРЅСЋС”."""
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    rc = 0
    for src in cfg["sources"]:
        if src["short"] not in (cfg.get("booking") or {}).get("sources", []):
            continue
        client = client_for(src)
        try:
            client.login()
            print(f"{src['short']}: РІС…С–Рґ OK; РґРЅС– Р· РІР°С€РёРјРё Р±СЂРѕРЅСЏРјРё: {sorted(client.my_days()) or '-'}")
        except T4UError as exc:
            print(f"{src['short']}: РџРћРњРР›РљРђ {exc}")
            rc = 1
    return rc


# ---------------------------------------------------------------- СЃРёРЅС…СЂРѕРЅС–Р·Р°С†С–СЏ Р· РєР°Р»РµРЅРґР°СЂРµРј

def _dt_from(it: dict, keys: tuple[str, ...], day_hint: str = "") -> datetime | None:
    """Р”С–СЃС‚Р°С” РґР°С‚Сѓ-С‡Р°СЃ С–Р· Р±СЂРѕРЅС– tenis4u, Сѓ СЏРєРѕРјСѓ Р± РІРёРіР»СЏРґС– РІРѕРЅР° РЅРµ Р±СѓР»Р°."""
    for k in keys:
        v = it.get(k)
        if isinstance(v, str) and len(v) >= 16:
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00").replace("/", "-")[:25])
            except ValueError:
                continue
        if isinstance(v, dict) and "hour" in v and day_hint:
            return datetime.fromisoformat(f"{day_hint}T{int(v['hour']):02d}:{int(v.get('minute', 0)):02d}")
        if isinstance(v, str) and len(v) in (5, 8) and ":" in v and day_hint:
            return datetime.fromisoformat(f"{day_hint}T{v[:5]}")
    return None


def _name_from(it: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        v = it.get(k)
        if isinstance(v, dict):
            v = v.get("name") or v.get("title")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _shape(it: dict) -> str:
    """Р›РёС€Рµ РЅР°Р·РІРё РїРѕР»С–РІ (Р±РµР· Р·РЅР°С‡РµРЅСЊ) - РґР»СЏ РґС–Р°РіРЅРѕСЃС‚РёРєРё РІ РїСѓР±Р»С–С‡РЅРѕРјСѓ Р¶СѓСЂРЅР°Р»С–."""
    return ", ".join(f"{k}{'{' + ','.join(v.keys()) + '}' if isinstance(v, dict) else ''}" for k, v in it.items())


def collect_my_bookings(cfg: dict, tz: ZoneInfo) -> list[tuple[dict, dict, str]]:
    """[(item РґР»СЏ make_ics, src, РїСЂРёРјС–С‚РєР°)] СѓСЃС–С… РІР°С€РёС… РјР°Р№Р±СѓС‚РЅС–С… Р±СЂРѕРЅРµР№ Сѓ РєР»СѓР±Р°С… Р· config.json."""
    now = datetime.now(tz)
    out: list[tuple[dict, dict, str]] = []
    seen: set[tuple] = set()

    t4u_srcs = [s for s in cfg["sources"] if s.get("type") == "tenis4u" and s.get("enabled", True)]
    if t4u_srcs and os.environ.get("TENIS4U_LOGIN"):
        client = Tenis4u(t4u_srcs[0])
        client.login()
        shown: set[str] = set()
        for path, it in client.my_reservations():
            if path not in shown:
                shown.add(path)
                print(f"tenis4u {path}: РїРѕР»СЏ Р±СЂРѕРЅС–: {_shape(it)}")
            status = str(it.get("status") or it.get("reservation_status") or "").upper()
            if any(s in status for s in ("CANCEL", "DELETED", "REJECT")):
                continue
            day = ""
            for k in ("date", "day", "start_date"):
                if isinstance(it.get(k), str) and len(it[k]) >= 10:
                    day = it[k][:10].replace("/", "-")
                    break
            start = _dt_from(it, ("start_time", "starts_at", "startTime", "start", "begin", "begin_time"), day)
            end = _dt_from(it, ("end_time", "ends_at", "endTime", "end", "end_hour"), day)
            if not start or not end:
                print(f"tenis4u {path}: РЅРµ Р·РјС–Рі СЂРѕР·С–Р±СЂР°С‚Рё С‡Р°СЃ Р±СЂРѕРЅС–")
                continue
            start = start.astimezone(tz) if start.tzinfo else start.replace(tzinfo=tz)
            end = end.astimezone(tz) if end.tzinfo else end.replace(tzinfo=tz)
            if end <= now:
                continue
            c = it.get("court")
            court_id = it.get("court_id") or (c.get("id") if isinstance(c, dict) else None)
            club = _name_from(it, ("court_name", "courtName", "court", "club_name", "clubName", "club"))
            src = next((s for s in t4u_srcs if court_id and int(s["court_id"]) == int(court_id)), None)
            if not src:
                src = next((s for s in t4u_srcs if club and (s["name"].lower() in club.lower()
                                                            or club.lower() in s["name"].lower())), None)
            src = src or {"short": "tenis4u", "name": club or "tenis4u", "type": "tenis4u"}
            station = _name_from(it, ("station_name", "stationName", "station")) or "РєРѕСЂС‚"
            station = station.split(": ", 1)[-1]
            station = station[:1].upper() + station[1:]
            key = (start.isoformat(), src["short"])
            if key in seen:
                continue
            seen.add(key)
            uid = it.get("public_uuid") or it.get("uuid") or it.get("id")
            item = {"day": start.date().isoformat(), "start": start.hour * 60 + start.minute,
                    "end": start.hour * 60 + start.minute + int((end - start).total_seconds() // 60),
                    "court": f"{src['short']}{SEP}{station}", "src": src["short"], "uuid": str(uid) if uid else None}
            price = it.get("total_price") or it.get("price") or it.get("cost")
            out.append((item, src, f"Р¦С–РЅР°: {price} zЕ‚" if price else ""))

    tt_srcs = [s for s in cfg["sources"] if s.get("type") == "twojtenis" and s.get("enabled", True)]
    if tt_srcs and os.environ.get("TWOJTENIS_LOGIN"):
        client = Twojtenis(tt_srcs[0])
        client.login()
        for b in client.my_bookings():
            day, st, en = str(b.get("date"))[:10], str(b.get("startTime"))[:5], str(b.get("endTime"))[:5]
            start = datetime.fromisoformat(f"{day}T{st}").replace(tzinfo=tz)
            s_min = int(st[:2]) * 60 + int(st[3:5])
            e_min = int(en[:2]) * 60 + int(en[3:5]) or 24 * 60
            if start + timedelta(minutes=e_min - s_min) <= now:
                continue
            src = next((s for s in tt_srcs if s["club_id"] == b.get("clubId")), None) \
                or {"short": "twojtenis", "name": b.get("clubName") or "twojtenis", "type": "twojtenis"}
            item = {"day": day, "start": s_min, "end": e_min, "src": src["short"],
                    "court": f"{src['short']}{SEP}{b.get('locationName') or 'РєРѕСЂС‚'}", "uuid": b.get("id")}
            note = []
            if b.get("price"):
                note.append(f"Р¦С–РЅР°: {b['price']} zЕ‚")
            if b.get("cancelUntil"):
                item["cancel_until"] = str(b["cancelUntil"])
                note.append(cancel_text(item, tz, 24))
            out.append((item, src, "\n".join(note)))
    return sorted(out, key=lambda x: (x[0]["day"], x[0]["start"]))


PTMATES_URL = "https://ptmates.com"
BLONIA_REFRESH_SEC = 3 * 3600  # СЏРє С‡Р°СЃС‚Рѕ РїРµСЂРµС‡РёС‚СѓРІР°С‚Рё Р±СЂРѕРЅС– BЕ‚onia (РІС…С–Рґ С‡РµСЂРµР· Р±СЂР°СѓР·РµСЂ, С‚РѕР¶ РЅРµ С‡Р°СЃС‚С–С€Рµ)


_PT_CSRF: list[str] = []


def _pt_csrf() -> str:
    """CSRF-С‚РѕРєРµРЅ, СЏРєРёР№ СЃР°Р№С‚ РєР»Р°РґРµ РІ <meta name="csrf-token"> (С‚Р°Рє СЃР°РјРѕ СЂРѕР±РёС‚СЊ СЃР°Рј Р±СЂР°СѓР·РµСЂ)."""
    if not _PT_CSRF:
        import re
        req = urllib.request.Request(PTMATES_URL + "/calendar", headers={
            "Cookie": os.environ.get("PTMATES_COOKIE", "").strip(), "User-Agent": "padel-slot-monitor/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            html = resp.read().decode("utf-8", "replace")
        m = re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)', html) or \
            re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']csrf-token', html)
        _PT_CSRF.append(m.group(1) if m else "")
    return _PT_CSRF[0]


def _pt(method: str, path: str, body=None):
    """Р—Р°РїРёС‚ РґРѕ PTmates Р· РІР°С€РѕСЋ СЃРµСЃС–С”СЋ (СЃРµРєСЂРµС‚ PTMATES_COOKIE)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Cookie": os.environ.get("PTMATES_COOKIE", "").strip(), "Accept": "application/json",
               "Content-Type": "application/json", "User-Agent": "padel-slot-monitor/1.0"}
    if method != "GET":
        headers.update({"X-CSRF-Token": _pt_csrf(), "Origin": PTMATES_URL, "Referer": f"{PTMATES_URL}/calendar"})
    req = urllib.request.Request(PTMATES_URL + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise T4UError(f"PTmates HTTP {exc.code} {method} {path}: {exc.read()[:200]!r}") from None
    return json.loads(raw) if raw.strip() else {}


def _norm(s: str) -> str:
    return str(s or "").lower().replace("Е‚", "l").replace("Гі", "o").replace("Е„", "n").strip()


def ptmates_publish(data: dict, state: dict, cfg: dict, tz: ZoneInfo) -> bool:
    """1) Р±СЂРѕРЅС– BЕ‚onia -> С–РіСЂРё РІ PTmates (В«Blonia SportВ», Р±РµР· РєР»СѓР±Сѓ);
    2) С–РіСЂРё Р· tenis4u, СЏРєРёРјРё РІРё РєРµСЂСѓС”С‚Рµ, -> РїСѓР±Р»С–С‡РЅС–; 3) Сѓ РІСЃС– РІР°С€С– С–РіСЂРё РґРѕРґР°С” РІР°СЃ СѓС‡Р°СЃРЅРёРєРѕРј.
    РџРѕРІРµСЂС‚Р°С” True, СЏРєС‰Рѕ С‰РѕСЃСЊ Р·РјС–РЅРёРІ (С‚РѕРґС– РєР°Р»РµРЅРґР°СЂ С‚СЂРµР±Р° РїРµСЂРµС‡РёС‚Р°С‚Рё)."""
    me = str(data.get("currentUserContactId") or "")
    if not me:
        return False
    now = datetime.now(tz)
    pm = state.setdefault("ptmates", {"hash": {}})
    changed = False

    # --- BЕ‚onia: РІР°С€С– Р±СЂРѕРЅС– Р· twojtenis (РєРµС€ РЅР° 3 РіРѕРґ; СЂР°РЅС–С€Рµ, СЏРєС‰Рѕ Р±РѕС‚ С‰РѕР№РЅРѕ Р·Р°Р±СЂРѕРЅСЋРІР°РІ BЕ‚onia)
    tt_src = next((s for s in cfg["sources"] if s.get("type") == "twojtenis" and s.get("enabled", True)), None)
    cache = pm.setdefault("blonia", {"ts": 0, "items": []})
    bot_new = any(x.get("src") == (tt_src or {}).get("short") and x.get("uuid") not in
                  {i["id"] for i in cache["items"]} for x in active_bookings(bstate(state), now))
    if tt_src and os.environ.get("TWOJTENIS_LOGIN") and (time.time() - cache["ts"] > BLONIA_REFRESH_SEC or bot_new):
        try:
            c = Twojtenis(tt_src)
            c.login()
            cache["items"] = [{"id": str(b.get("id")), "day": str(b.get("date"))[:10],
                               "start": str(b.get("startTime"))[:5], "end": str(b.get("endTime"))[:5],
                               "court": b.get("locationName") or "", "price": b.get("price")}
                              for b in c.my_bookings() if b.get("clubId") == tt_src["club_id"]]
            cache["ts"] = time.time()
        except T4UError as exc:
            log(f"ptmates/blonia: {exc}")
    created = pm.setdefault("created", {})  # twojtenis id -> С‚Р°Рє, РіСЂСѓ РІ PTmates СѓР¶Рµ СЃС‚РІРѕСЂРµРЅРѕ
    existing = {(str(r.get("starts_at"))[:16], _norm(r.get("title"))) for r in data["reservations"]}
    for b in cache["items"]:
        start = f"{b['day']}T{b['start']}"
        if b["id"] in created or datetime.fromisoformat(start).replace(tzinfo=tz) <= now:
            continue
        if any(s == start and "blonia" in t for s, t in existing):
            created[b["id"]] = "found"
            continue
        dur = (int(b["end"][:2]) * 60 + int(b["end"][3:5]) or 24 * 60) - (int(b["start"][:2]) * 60 + int(b["start"][3:5]))
        try:
            _pt("POST", "/api/games/custom", {"title": "Blonia Sport", "station_name": b["court"], "starts_at": start,
                                              "duration_minutes": str(dur), "price": str(b.get("price") or ""),
                                              "club_id": None})
            created[b["id"]] = "created"
            changed = True
            print(f"ptmates: СЃС‚РІРѕСЂРµРЅРѕ РіСЂСѓ BЕ‚onia {start} {b['court']}")
        except T4UError as exc:
            log(f"ptmates/custom: {exc}")
    if changed:  # РїРµСЂРµС‡РёС‚СѓС”РјРѕ, С‰РѕР± РЅРѕРІС– С–РіСЂРё РїСЂРѕР№С€Р»Рё РєСЂРѕРє В«РґРѕРґР°С‚Рё РІР°СЃ СѓС‡Р°СЃРЅРёРєРѕРјВ»
        data.update(_pt("GET", "/api/calendar/bootstrap"))

    # --- РїСѓР±Р»С–С‡РЅС–СЃС‚СЊ С– СѓС‡Р°СЃС‚СЊ
    for r in data["reservations"]:
        rid = str(r.get("id"))
        if not r.get("can_manage") or str(r.get("ends_at", ""))[:19] <= now.strftime("%Y-%m-%dT%H:%M:%S"):
            continue
        if any(s in str(r.get("status") or "").upper() for s in ("CANCEL", "DELETED", "REJECT")):
            continue
        players = list(data.get("playersByReservationId", {}).get(rid) or [])
        privacy = str(data.get("privacyByReservationId", {}).get(rid) or "PERSONAL").upper()
        from_t4u = str(r.get("source") or "").upper() == "API"
        want_public = from_t4u or "blonia" in _norm(r.get("title"))  # tenis4u С– Blonia Sport -> РїСѓР±Р»С–С‡РЅС–
        if me in players and (privacy == "PUBLIC" or not want_public):
            continue
        invites = data.get("invitesByReservationId", {}).get(rid) or []
        order = list(data.get("playerOrderByReservationId", {}).get(rid) or [f"pid:{p}" for p in players])
        new_privacy = "PUBLIC" if want_public else privacy
        body = {
            "players": [me] + [p for p in players if p != me],
            "is_public": new_privacy == "PUBLIC",
            "privacy_mode": new_privacy,
            "community_ids": [] if new_privacy != "COMMUNITY" else
            list(data.get("reservationCommunityIdsByReservationId", {}).get(rid) or []),
            "max_players": int(data.get("maxPlayersByReservationId", {}).get(rid) or 4),
            "invited_user_ids": [i["user_id"] for i in invites if i.get("user_id")
                                 and str(i.get("status") or "").upper() not in ("DECLINED", "CANCELED")],
            "player_order": [f"pid:{me}"] + [k for k in order if k != f"pid:{me}"],
        }
        try:
            _pt("POST", f"/api/reservations/{rid}/manage", body)
            changed = True
            print(f"ptmates: {r.get('title')} {str(r.get('starts_at'))[:16]}: "
                  f"{'РїСѓР±Р»С–С‡РЅР°, ' if want_public and privacy != 'PUBLIC' else ''}СѓС‡Р°СЃРЅРёРє {'РґРѕРґР°РЅРѕ' if me not in players else 'С”'}")
        except T4UError as exc:
            log(f"ptmates/manage {rid[:8]}: {exc}")
    return changed


def ptmates_sync() -> int:
    """Р†РіСЂРё Р· РєР°Р»РµРЅРґР°СЂСЏ PTmates (СЃРІРѕС— С– С‚С–, РєСѓРґРё РІР°СЃ Р·Р°РїСЂРѕСЃРёР»Рё) -> РєР°Р»РµРЅРґР°СЂ iCloud.
    Р”РѕРґР°С”/РѕРЅРѕРІР»СЋС” РјР°Р№Р±СѓС‚РЅС–, РїСЂРёР±РёСЂР°С” СЃРєР°СЃРѕРІР°РЅС– Р°Р±Рѕ РІС–РґС…РёР»РµРЅС–. РљСѓРєР° СЃРµСЃС–С— - СЃРµРєСЂРµС‚ PTMATES_COOKIE."""
    import hashlib
    import icloud_cal
    cookie = os.environ.get("PTMATES_COOKIE", "").strip()
    if not cookie or not icloud_cal.configured():
        return 0
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg["timezone"])
    state = load_state()
    pm = state.setdefault("ptmates", {"hash": {}})
    req = urllib.request.Request(f"{PTMATES_URL}/api/calendar/bootstrap", headers={
        "Cookie": cookie, "Accept": "application/json", "User-Agent": "padel-slot-monitor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data.get("reservations"), list):
            raise ValueError("РЅРµРјР°С” reservations")
    except Exception as exc:  # noqa: BLE001 - СЃРµСЃС–СЏ Р·Р°РєС–РЅС‡РёР»Р°СЃСЊ (СЂРµРґРёСЂРµРєС‚ РЅР° /login) Р°Р±Рѕ СЃР°Р№С‚ РЅРµРґРѕСЃС‚СѓРїРЅРёР№
        log(f"ptmates: {type(exc).__name__}: {str(exc)[:150]}")
        n = pm.get("fails", 0) + 1
        pm["fails"] = n
        if n == 3 and not pm.get("warned"):
            pm["warned"] = True
            tg_send("вљ пёЏ PTmates: РЅРµ РІРґР°С”С‚СЊСЃСЏ РїСЂРѕС‡РёС‚Р°С‚Рё РєР°Р»РµРЅРґР°СЂ (СЃС…РѕР¶Рµ, СЃРµСЃС–СЏ Р·Р°РєС–РЅС‡РёР»Р°СЃСЊ). "
                    "РЈРІС–Р№РґС–С‚СЊ РЅР° ptmates.com С– РѕРЅРѕРІС–С‚СЊ СЃРµРєСЂРµС‚ PTMATES_COOKIE.")
        save_state(state)
        return 1
    pm.pop("fails", None)
    pm.pop("warned", None)
    try:
        ptmates_publish(data, state, cfg, tz)
    except Exception as exc:  # noqa: BLE001 - РїСѓР±Р»С–РєР°С†С–СЏ РЅРµ РјР°С” Р»Р°РјР°С‚Рё РєР°Р»РµРЅРґР°СЂ
        log(f"ptmates_publish: {type(exc).__name__}: {exc}")
    # РіСЂР° BЕ‚onia РІ PTmates = Р±СЂРѕРЅСЊ twojtenis: С‚Р° СЃР°РјР° РїРѕРґС–СЏ РІ РєР°Р»РµРЅРґР°СЂС– (Р±РµР· РґСѓР±Р»С–РєР°С‚Р°)
    blonia_uid = {f"{b['day']}T{b['start']}": b["id"] for b in (pm.get("blonia") or {}).get("items", [])}

    now = datetime.now(tz)
    cal = None
    me = str(data.get("currentUserName") or "")
    seen: set[str] = set()
    changes: list[str] = []
    for r in data["reservations"]:
        try:
            start = datetime.fromisoformat(str(r["starts_at"])[:19]).replace(tzinfo=tz)
            end = datetime.fromisoformat(str(r["ends_at"])[:19]).replace(tzinfo=tz)
        except (KeyError, ValueError):
            continue
        if end <= now:
            continue
        rid = str(r.get("id"))
        if "blonia" in _norm(r.get("title")) and blonia_uid.get(str(r.get("starts_at"))[:16]):
            rid = blonia_uid[str(r.get("starts_at"))[:16]]
        uid = f"{rid}@padel-monitor"
        seen.add(uid)
        status = str(r.get("status") or "").upper()
        part = str(r.get("participation_status") or "").upper()
        dead = any(s in status for s in ("CANCEL", "DELETED", "REJECT")) or part in ("DECLINED", "REJECTED", "LEFT", "REMOVED")
        club = str(r.get("title") or "РџР°РґРµР»").strip()
        station = str(r.get("station_name") or "").strip()
        station = station[:1].upper() + station[1:]
        day = start.date().isoformat()
        label = f"{fmt_day(day)} {start:%H:%M}вЂ“{end:%H:%M} В· {club}"
        if dead:
            if rid in pm["hash"]:
                cal = cal or icloud_cal.find_calendar()
                icloud_cal.delete_event(uid, cal)
                pm["hash"].pop(rid, None)
                changes.append(f"рџ—‘ {label}")
            continue
        owner = str(r.get("owner_name") or "").strip()
        note = []
        if owner and owner != me:
            note.append(f"Р‘СЂРѕРЅСЋРІР°РІ: {owner}")
        if r.get("price"):
            note.append(f"Р¦С–РЅР°: {r['price']} zЕ‚")
        item = {"day": day, "start": start.hour * 60 + start.minute,
                "end": start.hour * 60 + start.minute + int((end - start).total_seconds() // 60),
                "court": f"PTmates{SEP}{station or 'РєРѕСЂС‚'}", "src": "PTmates", "uuid": rid}
        src = {"name": club, "type": "ptmates", "booking_url": f"{PTMATES_URL}/calendar"}
        h = hashlib.sha1(json.dumps([item, club, note], ensure_ascii=False).encode()).hexdigest()
        if (pm["hash"].get(rid) or {}).get("h") == h:
            continue
        cal = cal or icloud_cal.find_calendar()
        icloud_cal.put_event(uid, make_ics(item, src, tz, "\n".join(note)), cal)
        changes.append(("вњЏпёЏ " if rid in pm["hash"] else "вћ• ") + label + (f" (Р±СЂРѕРЅСЋРІР°РІ {owner})" if owner and owner != me else ""))
        pm["hash"][rid] = {"h": h, "end": end.isoformat()}
    # С–РіСЂРё, С‰Рѕ Р·РЅРёРєР»Рё Р· PTmates РґРѕ СЃРІРѕРіРѕ РїРѕС‡Р°С‚РєСѓ (РІРёРґР°Р»РµРЅС–) -> РїСЂРёР±РёСЂР°С”РјРѕ Р· РєР°Р»РµРЅРґР°СЂСЏ
    for rid in [k for k in pm["hash"] if f"{k}@padel-monitor" not in seen]:
        info = pm["hash"].pop(rid)
        if isinstance(info, dict) and info.get("end", "") > now.isoformat():
            cal = cal or icloud_cal.find_calendar()
            icloud_cal.delete_event(f"{rid}@padel-monitor", cal)
            changes.append("рџ—‘ РіСЂСѓ РїСЂРёР±СЂР°РЅРѕ Р· PTmates вЂ” РІРёРґР°Р»РµРЅРѕ Р· РєР°Р»РµРЅРґР°СЂСЏ")
    save_state(state)
    for c in changes:  # Р±РµР· РїРѕРІС–РґРѕРјР»РµРЅСЊ Сѓ Telegram: Р»РёС€Рµ РІ Р¶СѓСЂРЅР°Р»
        print(f"ptmates: {c}")
    print(f"ptmates: С–РіРѕСЂ {len(data['reservations'])}, Р·РјС–РЅ Сѓ РєР°Р»РµРЅРґР°СЂС–: {len(changes)}")
    return 0


def sync_calendar() -> int:
    """Р”РѕРґР°С” РІ РєР°Р»РµРЅРґР°СЂ iCloud СѓСЃС– РІР°С€С– РјР°Р№Р±СѓС‚РЅС– Р±СЂРѕРЅС– (РїРѕРІС‚РѕСЂРЅРёР№ Р·Р°РїСѓСЃРє РЅРµ СЃС‚РІРѕСЂСЋС” РґСѓР±Р»С–РєР°С‚С–РІ)."""
    import icloud_cal
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg["timezone"])
    if not icloud_cal.configured():
        print("РЅРµРјР°С” СЃРµРєСЂРµС‚С–РІ ICLOUD_*")
        return 1
    try:
        found = collect_my_bookings(cfg, tz)
        cal = icloud_cal.find_calendar()
    except (T4UError, icloud_cal.CalError) as exc:
        print(f"РџРћРњРР›РљРђ: {exc}")
        tg_send(f"вљ пёЏ РЎРёРЅС…СЂРѕРЅС–Р·Р°С†С–СЏ РєР°Р»РµРЅРґР°СЂСЏ РЅРµ РІРґР°Р»Р°СЃСЏ: {exc}")
        return 1
    lines, failed = [], 0
    for item, src, note in found:
        line = f"вЂў {fmt_day(item['day'])} {to_hm(item['start'])}вЂ“{to_hm(item['end'] % (24 * 60))} В· " \
               f"{src.get('name', item['src'])} В· {item['court'].split(SEP, 1)[-1]}"
        ok = True
        try:
            icloud_cal.put_event(cal_uid(item), make_ics(item, src, tz, note), cal)
            lines.append(line)
        except icloud_cal.CalError as exc:
            ok = False
            failed += 1
            lines.append(f"{line} вљ пёЏ {exc}")
        print(f"{item['day']} {to_hm(item['start'])} {src.get('short')}: {'OK' if ok else 'РџРћРњРР›РљРђ'}")
    print(f"Р·РЅР°Р№РґРµРЅРѕ РјР°Р№Р±СѓС‚РЅС–С… Р±СЂРѕРЅРµР№: {len(found)}, РїРѕРјРёР»РѕРє: {failed}")
    tg_send("рџ“… Р”РѕРґР°РЅРѕ РІ РєР°Р»РµРЅРґР°СЂ РІР°С€С– РјР°Р№Р±СѓС‚РЅС– Р±СЂРѕРЅС–:\n" + "\n".join(lines) if lines
            else "рџ“… РњР°Р№Р±СѓС‚РЅС–С… Р±СЂРѕРЅРµР№ РЅРµ Р·РЅР°Р№РґРµРЅРѕ.")
    return 1 if failed else 0


def selftest() -> int:
    """РџРµСЂРµРІС–СЂРєР° Р»РѕРіС–РєРё РЅР° РІРёРіР°РґР°РЅРёС… РґР°РЅРёС…: РЅС–С‡РѕРіРѕ РЅРµ Р±СЂРѕРЅСЋС” С– РЅРµ РЅР°РґСЃРёР»Р°С”."""
    global do_book, tg_send, tg_edit
    sent: list[str] = []
    booked: list[dict] = []

    def fake_book(item, *_a):
        booked.append(dict(item))
        item.update(uuid="test", cost=150.0, status="active")
        return True, ""

    def fake_send(text, buttons=None):
        sent.append(text + (f"  {buttons}" if buttons else ""))
        return len(sent)

    do_book, tg_send, tg_edit = fake_book, fake_send, lambda *a, **k: None
    os.environ.setdefault("TENIS4U_LOGIN", "test")
    os.environ.setdefault("TWOJTENIS_LOGIN", "test")
    os.environ["PADEL_SELFTEST"] = "1"
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg["timezone"])
    now = datetime(2026, 11, 20, 12, 0, tzinfo=tz)  # РїС‚
    sources = {s["short"]: s for s in cfg["sources"]}
    blocks = [
        {"day": "2026-11-21", "court": "Fame В· Padel hala", "start": 18 * 60, "end": 20 * 60},     # 30 РіРѕРґ -> Р°РІС‚Рѕ
        {"day": "2026-11-21", "court": "PadelHouse В· Padel 1", "start": 19 * 60, "end": 21 * 60},  # С‚РѕР№ СЃР°РјРёР№ РґРµРЅСЊ, РїС–Р·РЅС–С€Рµ
        {"day": "2026-11-20", "court": "Fame В· Padel hala", "start": 20 * 60, "end": 21 * 60 + 30},  # 8 РіРѕРґ -> РїРёС‚Р°С‚Рё
        {"day": "2026-11-24", "court": "BЕ‚onia В· Padel 1", "start": 18 * 60, "end": 20 * 60},      # twojtenis, Р°РІС‚Рѕ
        {"day": "2026-11-25", "court": "PadelHouse В· Padel 2", "start": 20 * 60, "end": 23 * 60},  # Р°РІС‚Рѕ, 2 РіРѕРґ
    ]
    state: dict = {}
    quiet = process(blocks, cfg, state, now, tz, sources, {})
    assert [x["day"] for x in booked] == ["2026-11-21", "2026-11-24", "2026-11-25"], booked
    assert booked[0]["court"].startswith("Fame") and booked[0]["end"] - booked[0]["start"] == 120
    assert booked[1]["court"].startswith("BЕ‚onia")
    assert booked[2]["start"] == 20 * 60 and booked[2]["end"] == 22 * 60
    assert list(state["booking"]["offers"].values())[0]["end"] == 21 * 60 + 30  # Р±Р»РѕРє 1.5 РіРѕРґ -> 1.5 РіРѕРґ
    assert quiet == {"2026-11-20", "2026-11-21", "2026-11-24", "2026-11-25"}, quiet
    # РїРѕРІС‚РѕСЂРЅРёР№ Р·Р°РїСѓСЃРє РЅС–С‡РѕРіРѕ РЅРѕРІРѕРіРѕ РЅРµ СЂРѕР±РёС‚СЊ
    n = len(sent)
    process(blocks, cfg, state, now, tz, sources, {})
    assert len(sent) == n and len(booked) == 3
    ics = make_ics(booked[0] | {"uuid": "abc"}, sources["Fame"], tz, "Р¦С–РЅР°: 150 zЕ‚").decode("utf-8")
    assert "DTSTART:20261121T170000Z" in ics and "DTEND:20261121T190000Z" in ics, ics  # 18:00 Р’Р°СЂС€Р°РІР° = 17:00 UTC
    assert "SUMMARY:РџР°РґРµР» В· Fame Sport Club В· Padel hala" in ics and "TRIGGER:-PT2H" in ics, ics
    print("\n---\n".join(sent))
    print("selftest OK")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "poll":
        poll(int(sys.argv[2]) if len(sys.argv) > 2 else 60)
    elif cmd == "check":
        sys.exit(check())
    elif cmd == "selftest":
        sys.exit(selftest())
    elif cmd == "book":  # СЂСѓС‡РЅРµ С‚РµСЃС‚РѕРІРµ Р±СЂРѕРЅСЋРІР°РЅРЅСЏ: book <short> "<РєРѕСЂС‚>" YYYY-MM-DD HH:MM HH:MM
        _cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        _short, _court, _day, _st, _en = sys.argv[2:7]
        _src = next(s for s in _cfg["sources"] if s["short"] == _short)
        _c = client_for(_src)
        try:
            _c.login()
            if isinstance(_c, Twojtenis):
                _club = _c._req("GET", f"{_c.api}/Clubs/{_c.club}")
                _loc = next(l for l in _club["locations"] if l["name"].strip().lower() == _court.strip().lower())
                _res = _c.book(_loc["id"], _loc["name"], _day, f"{_st}:00", f"{_en}:00")
            else:
                raise T4UError("СЂСѓС‡РЅРµ С‚РµСЃС‚РѕРІРµ Р±СЂРѕРЅСЋРІР°РЅРЅСЏ Р·СЂРѕР±Р»РµРЅРµ Р»РёС€Рµ РґР»СЏ twojtenis")
        except T4UError as exc:
            print(f"{_short}: РџРћРњРР›РљРђ Р±СЂРѕРЅСЋРІР°РЅРЅСЏ: {exc}")
            tg_send(f"вљ пёЏ РўРµСЃС‚РѕРІРµ Р±СЂРѕРЅСЋРІР°РЅРЅСЏ РЅРµ РІРґР°Р»РѕСЃСЏ\n{_src['name']} В· {_court}\n{fmt_day(_day)} {_st}вЂ“{_en}\nРџСЂРёС‡РёРЅР°: {exc}")
            sys.exit(1)
        _bid = _res.get("id")
        print(f"{_short}: Р—РђР‘Р РћРќР¬РћР’РђРќРћ id={_bid} С†С–РЅР°={_res.get('_cost')} cancelUntil={_res.get('cancelUntil')}")
        _cu = f"\nРЎРєР°СЃСѓРІР°С‚Рё РјРѕР¶РЅР° РґРѕ: {_res.get('cancelUntil')}" if _res.get("cancelUntil") else ""
        tg_send(f"вњ… РўРµСЃС‚РѕРІР° Р±СЂРѕРЅСЊ\n{_src['name']} В· {_court}\n{fmt_day(_day)} {_st}вЂ“{_en}\n"
                f"Р¦С–РЅР°: {_res.get('_cost')} zЕ‚ (РѕРїР»Р°С‚Р° РІ РєР»СѓР±С–){_cu}\n{my_url(_src)}")
        _h = lambda s: int(s[:2]) * 60 + int(s[3:5])  # noqa: E731
        send_calendar({"day": _day, "start": _h(_st), "end": _h(_en), "court": f"{_short}{SEP}{_court}",
                       "src": _short, "uuid": _bid}, _src, ZoneInfo(_cfg["timezone"]),
                      f"Р¦С–РЅР°: {_res.get('_cost')} zЕ‚ (РѕРїР»Р°С‚Р° РІ РєР»СѓР±С–){_cu}")
    elif cmd == "cash":  # РІР°С€С– РјР°Р№Р±СѓС‚РЅС– Р±СЂРѕРЅС– tenis4u В«РґРѕ РѕРїР»Р°С‚РёВ» -> РѕРїР»Р°С‚Р° РіРѕС‚С–РІРєРѕСЋ
        _cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        _src = next(s for s in _cfg["sources"] if s.get("type") == "tenis4u")
        _c = Tenis4u(_src)
        _c.login()
        _now = datetime.now(ZoneInfo(_cfg["timezone"])).strftime("%Y-%m-%dT%H:%M")
        _n = 0
        for _p, _it in _c.my_reservations():
            _st = str(_it.get("start_time") or "")[:16]
            _pay = str(_it.get("my_payment_status") or "").upper()
            print(f"{_st} {_it.get('court_name')} СЃС‚Р°С‚СѓСЃ={_it.get('status')} РѕРїР»Р°С‚Р°={_pay or '-'} СЂРѕР»СЊ={_it.get('my_role')}")
            if _st > _now and needs_cash(_it) and _it.get("public_uuid"):
                try:
                    _r = _c.pay_cash(_it["public_uuid"])
                    _online = isinstance(_r, dict) and (_r.get("payment") or {}).get("redirect_url")
                    print(f"  -> {'РєР»СѓР± РїРѕРІРµСЂРЅСѓРІ РѕРЅР»Р°Р№РЅ-РѕРїР»Р°С‚Сѓ!' if _online else 'РіРѕС‚С–РІРєР°'}")
                    _n += 0 if _online else 1
                except T4UError as exc:
                    print(f"  -> РџРћРњРР›РљРђ {exc}")
        tg_send(f"рџ’µ РћРїР»Р°С‚Сѓ РіРѕС‚С–РІРєРѕСЋ РѕР±СЂР°РЅРѕ РґР»СЏ {_n} Р±СЂРѕРЅРµР№ Сѓ tenis4u." if _n else "рџ’µ Р‘СЂРѕРЅРµР№ В«РґРѕ РѕРїР»Р°С‚РёВ» РЅРµ Р·РЅР°Р№РґРµРЅРѕ.")
    elif cmd == "sync":  # СѓСЃС– РІР°С€С– РјР°Р№Р±СѓС‚РЅС– Р±СЂРѕРЅС– -> РєР°Р»РµРЅРґР°СЂ iCloud
        sys.exit(sync_calendar())
    elif cmd == "ptmates-sync":  # С–РіСЂРё Р· PTmates -> РєР°Р»РµРЅРґР°СЂ iCloud
        sys.exit(ptmates_sync())
    elif cmd == "ics":  # Р»РёС€Рµ С„Р°Р№Р» РєР°Р»РµРЅРґР°СЂСЏ РґР»СЏ РІР¶Рµ С–СЃРЅСѓСЋС‡РѕС— Р±СЂРѕРЅС–: ics <short> "<РєРѕСЂС‚>" YYYY-MM-DD HH:MM HH:MM
        _cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        _short, _court, _day, _st, _en = sys.argv[2:7]
        _src = next(s for s in _cfg["sources"] if s["short"] == _short)
        _h = lambda s: int(s[:2]) * 60 + int(s[3:5])  # noqa: E731
        send_calendar({"day": _day, "start": _h(_st), "end": _h(_en), "court": f"{_short}{SEP}{_court}",
                       "src": _short, "uuid": (sys.argv[7] if len(sys.argv) > 7 and sys.argv[7] else None)},
                      _src, ZoneInfo(_cfg["timezone"]))
        print("РїРѕРґС–СЋ РєР°Р»РµРЅРґР°СЂСЏ РЅР°РґС–СЃР»Р°РЅРѕ")
    elif cmd == "probe":  # РґС–Р°РіРЅРѕСЃС‚РёРєР° Р·Р°РїРёС‚Сѓ Р±СЂРѕРЅСЋРІР°РЅРЅСЏ twojtenis (РґР°С‚Р° РІ РјРёРЅСѓР»РѕРјСѓ, Р±СЂРѕРЅСЊ РЅРµ СЃС‚РІРѕСЂСЋС”С‚СЊСЃСЏ)
        _cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        _src = next(s for s in _cfg["sources"] if s.get("type") == "twojtenis")
        _c = Twojtenis(_src)
        _c.login()
        _club = _c._req("GET", f"{_c.api}/Clubs/{_c.club}")
        _loc = next(l for l in _club["locations"] if l.get("type") == _src["court_type"])
        print(f"{_src['short']} probe: {_c.probe(_loc['id'], _loc['name'])}")
    else:
        print(__doc__)
