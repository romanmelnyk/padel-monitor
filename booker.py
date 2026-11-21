#!/usr/bin/env python3
"""Автобронювання кортів (tenis4u.pl) + кнопки в Telegram.

Правила (config.json -> booking):
  - не більше одного корту на день (бот не бронює день, де вже є ваша бронь у tenis4u);
  - до гри >= auto_min_lead_hours (30 год) -> бронює сам і пише, до коли можна безкоштовно скасувати;
  - менше -> надсилає пропозицію з кнопками «Забронювати» / «Пропустити»;
  - тривалість: prefer_minutes (2 год), якщо блок дозволяє, інакше мінімальний блок (1.5 год);
  - якщо в день кілька варіантів -> найраніший час;
  - не більше max_active_auto активних автоброней одночасно.

Запуск:
  python booker.py poll SECONDS  - слухає кнопки Telegram SECONDS секунд (між перевірками в monitor.yml)
  python booker.py check         - перевірка входу і цін без бронювання (вручну)

Логін і пароль tenis4u беруться з секретів TENIS4U_LOGIN / TENIS4U_PASSWORD.
Дані про броні зберігаються лише в data/state.json (кеш Actions), не в репозиторії.
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
SEP = " · "
UA_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
T4U_CLIENT = ("tenis", "secret")  # публічний OAuth-клієнт вебзастосунку tenis4u
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
    except urllib.error.HTTPError as exc:  # без URL: у ньому токен
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
            raise T4UError("немає секретів TENIS4U_LOGIN / TENIS4U_PASSWORD")
        basic = base64.b64encode(f"{T4U_CLIENT[0]}:{T4U_CLIENT[1]}".encode()).decode()
        form = "grant_type=password&username=" + urllib.parse.quote(user) + "&password=" + urllib.parse.quote(pw)
        res = self._req("POST", "/oauth/token", form=form, auth=f"Basic {basic}")
        self.token = res.get("access_token")
        if not self.token:
            raise T4UError("вхід не вдався (немає access_token)")

    def my_days(self) -> set[str]:
        """Дні, на які у вас уже є активна бронь у tenis4u (будь-який клуб)."""
        days: set[str] = set()
        for path in ("/api/v3/reservations/my", "/reservation/list"):
            try:
                res = self._req("GET", path)
            except T4UError as exc:
                log(f"tenis4u {path}: {exc}")
                continue
            items = res if isinstance(res, list) else next(
                (v for v in (res or {}).values() if isinstance(v, list)), [])
            for it in items:
                if not isinstance(it, dict):
                    continue
                status = str(it.get("status") or it.get("reservation_status") or "").upper()
                if any(s in status for s in ("CANCEL", "DELETED", "REJECT")):
                    continue
                for k in ("start_time", "starts_at", "startTime", "start_date", "date", "day"):
                    v = it.get(k)
                    if isinstance(v, str) and len(v) >= 10:
                        days.add(v[:10].replace("/", "-"))
                        break
        return days

    def tariff_id(self, station_id: int, start: datetime, end: datetime) -> tuple[int | None, float | None]:
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
        return (best or {}).get("id"), (best or {}).get("cost")

    def book(self, station_id: int, start: datetime, end: datetime) -> dict:
        tariff, cost = self.tariff_id(station_id, start, end)
        body = {
            "station_id": int(station_id),
            "start_time": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "declared_player_count": 1,
            "payment_strategy": "ORGANIZER_PAYS",
            "lightning": False,
            "tariff_id": tariff,
        }
        res = self._req("POST", "/api/v3/reservations", body=body)
        res = res if isinstance(res, dict) else {}
        res["_cost"] = cost
        return res

    def cancel(self, uuid: str) -> None:
        self._req("DELETE", f"/api/v3/reservations/{uuid}")


# ---------------------------------------------------------------- twojtenis.pl API

class Twojtenis:
    """Бронювання в клубі на twojtenis.pl. Вхід через невидимий браузер (tt_login.py)."""

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
                if isinstance(err, dict) and err.get("errors"):  # ASP.NET: {"errors": {"поле": ["причина"]}}
                    msg = f"{msg} {json.dumps(err['errors'], ensure_ascii=False)[:400]}"
            except json.JSONDecodeError:
                msg = raw[:200]
            path = urllib.parse.urlparse(url).path
            raise T4UError(f"HTTP {exc.code} {method} {path}: {msg}") from None

    def login(self) -> None:
        import tt_login
        try:
            self.token, _ = tt_login.get_token()
        except tt_login.LoginError as exc:
            raise T4UError(f"вхід у twojtenis: {exc}") from None
        self.svc = self._req("GET", f"{self.api}/Clubs/{self.club}/technical-group")["serviceUrl"].rstrip("/")

    def my_days(self) -> set[str]:
        today = date.today()
        q = urllib.parse.urlencode({"from": today.isoformat(), "to": (today + timedelta(days=40)).isoformat()})
        items = self._req("GET", f"{self.svc}/api/v1/bookings/my?{q}") or []
        return {str(b.get("date"))[:10] for b in items if isinstance(b, dict) and not b.get("isDeleted")}

    def book(self, location_id: str, location_name: str, day: str, start: str, end: str) -> dict:
        prof, price = self._prepare(location_id, day, start, end)
        body = self._body(prof, price, location_id, location_name, day, start, end)
        res = self._req("POST", f"{self.svc}/api/v1/Clubs/{self.club}/bookings", body)
        first = res[0] if isinstance(res, list) and res else res if isinstance(res, dict) else {}
        first["_cost"] = price["price"]
        return first

    def probe(self, location_id: str, location_name: str) -> str:
        """Діагностика: справжня ціна на майбутній день, але дата броні — вчора.
        Сервер спершу перевіряє формат полів; бронь у минулому створити неможливо."""
        future = (date.today() + timedelta(days=3)).isoformat()
        past = (date.today() - timedelta(days=1)).isoformat()
        prof, price = self._prepare(location_id, future, "21:00:00", "22:30:00")
        body = self._body(prof, price, location_id, location_name, past, "21:00:00", "22:30:00")
        nulls = [k for k, v in body.items() if v is None] + [f"requests.{k}" for k, v in body["requests"][0].items() if v is None]
        try:
            res = self._req("POST", f"{self.svc}/api/v1/Clubs/{self.club}/bookings", body)
            return f"НЕОЧІКУВАНО прийнято: {json.dumps(res, ensure_ascii=False)[:300]}"
        except T4UError as exc:
            return f"{exc} | порожні поля: {nulls or '-'}"

    def _prepare(self, location_id: str, day: str, start: str, end: str) -> tuple[dict, dict]:
        me = self._req("GET", f"{self.api}/Players/me")
        try:
            prof = self._req("GET", f"{self.api}/Clubs/{self.club}/players/{me['id']}")
        except T4UError as exc:
            if "HTTP 404" not in str(exc):
                raise
            prof = self._req("POST", f"{self.api}/Clubs/{self.club}/players/{me['id']}")
        if prof.get("isBlocked"):
            raise T4UError("ваш профіль у клубі заблоковано")
        calc = self._req("POST", f"{self.api}/Clubs/{self.club}/actions/calculate-price", {
            "clubId": self.club, "locationId": location_id, "start": start, "end": end, "days": [day],
            "multiSportCardsUsed": 0, "medicoverCardsUsed": 0, "playerId": prof["id"],
            "isSmartTennisMatch": False, "isOnlinePayment": False})
        price = (calc.get("prices") or [{}])[0]
        if not price.get("checksum") or price.get("failed"):
            raise T4UError(f"клуб не дав ціну на цей час: {json.dumps(calc, ensure_ascii=False)[:200]}")
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
            "bookerType": 0, "clubName": self.src.get("name", ""), "comment": "",
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
    b.setdefault("failed", [])     # ключі спроб, що не вдалися (не повторюємо)
    b.setdefault("seq", 0)
    return b


def next_id(b: dict) -> str:
    b["seq"] += 1
    return str(b["seq"])


def active_bookings(b: dict, now: datetime) -> list[dict]:
    return [x for x in b["bookings"] if x.get("status") == "active" and x["day"] >= now.date().isoformat()]


def my_url(src: dict) -> str:
    return src.get("booking_url", "") if src.get("type") == "twojtenis" else MY_RESERVATIONS_URL


def plan_for_block(block: dict, bcfg: dict) -> tuple[int, int]:
    length = block["end"] - block["start"]
    dur = bcfg.get("prefer_minutes", 120) if length >= bcfg.get("prefer_minutes", 120) else bcfg.get("min_minutes", 90)
    return block["start"], block["start"] + dur


def describe(item: dict, sources: dict) -> str:
    src = sources.get(item["src"], {})
    court = item["court"].split(SEP, 1)[-1]
    return (f"{src.get('name', item['src'])} · {court}\n"
            f"{fmt_day(item['day'])} {to_hm(item['start'])}–{to_hm(item['end'])}")


def cancel_deadline(item: dict, tz: ZoneInfo, hours: int) -> datetime:
    start = datetime.fromisoformat(f"{item['day']}T{to_hm(item['start'])}").replace(tzinfo=tz)
    return start - timedelta(hours=hours)


def do_book(item: dict, sources: dict, court_ids: dict, tz: ZoneInfo) -> tuple[bool, str]:
    """Повертає (успіх, текст)."""
    src = sources[item["src"]]
    station = court_ids.get(item["court"])
    if not station:
        return False, "не знаю id корту (зачекайте наступної перевірки)"
    start = datetime.fromisoformat(f"{item['day']}T{to_hm(item['start'])}").replace(tzinfo=tz)
    end = datetime.fromisoformat(f"{item['day']}T{to_hm(item['end'] % (24 * 60))}").replace(tzinfo=tz)
    if item["end"] >= 24 * 60:
        end += timedelta(days=1)
    client = client_for(src)
    try:
        client.login()
        if item["day"] in client.my_days():
            return False, f"на цей день у вас уже є бронь у {src.get('name', item['src'])}"
        if isinstance(client, Twojtenis):
            res = client.book(station, item["court"].split(SEP, 1)[-1], item["day"],
                              f"{to_hm(item['start'])}:00", f"{to_hm(item['end'] % (24 * 60))}:00")
        else:
            res = client.book(station, start, end)
    except T4UError as exc:
        return False, str(exc)
    uuid = res.get("public_uuid") or res.get("uuid") or res.get("id")
    if not uuid:
        return False, f"незрозуміла відповідь сервера: {json.dumps(res, ensure_ascii=False)[:300]}"
    item["uuid"] = str(uuid)
    item["cost"] = res.get("_cost")
    item["status"] = "active"
    if res.get("cancelUntil"):
        item["cancel_until"] = str(res["cancelUntil"])
    extra = ""
    status = str(res.get("status") or "")
    if status and status.upper() not in ("CONFIRMED", "ACTIVE", "CREATED"):
        extra = f"\nСтатус бронювання: {status} — перевірте в застосунку."
    return True, extra


def cancel_text(item: dict, tz: ZoneInfo, hours: int) -> str:
    """«Скасувати безкоштовно можна до …» — з відповіді клубу (twojtenis) або за налаштуванням."""
    dl = None
    if item.get("cancel_until"):
        try:
            dl = datetime.fromisoformat(item["cancel_until"].replace("Z", "+00:00"))
            dl = dl.replace(tzinfo=tz) if dl.tzinfo is None else dl.astimezone(tz)
        except ValueError:
            dl = None
    dl = dl or cancel_deadline(item, tz, hours)
    return f"Скасувати безкоштовно можна до {fmt_day(dl.date().isoformat())} {dl:%H:%M}."


def process(blocks: list[dict], cfg: dict, state: dict, now: datetime, tz: ZoneInfo,
            sources: dict, court_ids: dict) -> set[str]:
    """Викликається з collector.py після кожної перевірки. Повертає дні, про які не треба
    надсилати звичайні сповіщення (там уже є бронь або пропозиція)."""
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
            tg_send(f"⚠️ Автобронювання для {short} увімкнене, але немає секрету {env} у GitHub.")
    if not allowed:
        return set()

    today = now.date().isoformat()
    booked_days = {x["day"] for x in active_bookings(b, now)}
    # прибираємо старе
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
            tg_edit(o.get("msg"), "⌛ Слот уже зайняли:\n" + describe(o, sources))

    quiet = booked_days | open_offer_days | set(b["skipped_days"])
    # кандидати: найраніший блок у кожен день
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
                cost = f"\nЦіна: {item['cost']:g} zł (оплата в клубі)" if item.get("cost") else ""
                item["msg"] = tg_send(
                    f"✅ Забронював корт\n{describe(item, sources)}{cost}\n"
                    f"{cancel_text(item, tz, cancel_h)}{extra}\n"
                    f"{my_url(sources[item['src']])}",
                    [[("❌ Скасувати бронь", f"c:{item['id']}")]])
            else:
                b["failed"].append(item["key"])
                tg_send(f"⚠️ Не вдалося забронювати\n{describe(item, sources)}\nПричина: {extra}")
            continue
        # мало часу до гри (або досягнуто ліміту автоброней) -> питаємо
        oid = next_id(b)
        item["status"] = "open"
        why = "До гри менше ніж " + f"{auto_hours} год" if lead_h < auto_hours else f"Уже є {n_auto} автоброні"
        item["msg"] = tg_send(
            f"🎾 Вільний корт. Забронювати?\n{describe(item, sources)}\n"
            f"({why}, тому питаю. Скасувати пізніше ніж за {cancel_h} год до гри може бути неможливо.)",
            [[("✅ Забронювати", f"b:{oid}"), ("Пропустити", f"s:{oid}")]])
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
            tg_edit(mid, (msg.get("text") or "") + "\n\n(пропозиція вже неактуальна)")
            return
        if kind == "s":
            o["status"] = "skipped"
            b["skipped_days"].append(o["day"])
            tg_edit(mid, "Пропущено:\n" + describe(o, sources))
            return
        if any(x["day"] == o["day"] for x in active_bookings(b, now)):
            o["status"] = "gone"
            tg_edit(mid, "На цей день уже є бронь:\n" + describe(o, sources))
            return
        o["id"] = oid
        o["auto"] = False
        ok, extra = do_book(o, sources, court_ids, tz)
        if ok:
            o["status"] = "booked"
            b["bookings"].append(dict(o, status="active", msg=mid))
            cost = f"\nЦіна: {o['cost']:g} zł (оплата в клубі)" if o.get("cost") else ""
            tg_edit(mid, f"✅ Заброньовано\n{describe(o, sources)}{cost}\n"
                         f"{cancel_text(o, tz, (cfg.get('booking') or {}).get('cancel_limit_hours', 24))}"
                         f"{extra}\n{my_url(sources[o['src']])}",
                    [[("❌ Скасувати бронь", f"c:{oid}")]])
        else:
            o["status"] = "failed"
            tg_edit(mid, f"⚠️ Не вдалося забронювати\n{describe(o, sources)}\nПричина: {extra}")
        return

    if kind == "c":
        x = next((x for x in b["bookings"] if x.get("id") == oid and x.get("status") == "active"), None)
        if not x:
            tg_edit(mid, (msg.get("text") or "") + "\n\n(бронь не знайдено або вже скасовано)")
            return
        try:
            client = client_for(sources[x["src"]])
            client.login()
            client.cancel(x["uuid"])
        except T4UError as exc:
            tg_send(f"⚠️ Не вдалося скасувати бронь\n{describe(x, sources)}\nПричина: {exc}\n"
                    f"Скасуйте в застосунку: {my_url(sources[x['src']])}")
            return
        x["status"] = "cancelled"
        b["skipped_days"].append(x["day"])  # не бронювати цей день знову автоматично
        tg_edit(mid, "🗑 Бронь скасовано\n" + describe(x, sources))


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def poll(seconds: int) -> None:
    """Слухає натискання кнопок seconds секунд (long polling Telegram)."""
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
    """Ручна перевірка: вхід і дні з вашими бронями в кожному клубі. Нічого не бронює."""
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    rc = 0
    for src in cfg["sources"]:
        if src["short"] not in (cfg.get("booking") or {}).get("sources", []):
            continue
        client = client_for(src)
        try:
            client.login()
            print(f"{src['short']}: вхід OK; дні з вашими бронями: {sorted(client.my_days()) or '-'}")
        except T4UError as exc:
            print(f"{src['short']}: ПОМИЛКА {exc}")
            rc = 1
    return rc


def selftest() -> int:
    """Перевірка логіки на вигаданих даних: нічого не бронює і не надсилає."""
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
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg["timezone"])
    now = datetime(2026, 11, 20, 12, 0, tzinfo=tz)  # пт
    sources = {s["short"]: s for s in cfg["sources"]}
    blocks = [
        {"day": "2026-11-21", "court": "Fame · Padel hala", "start": 18 * 60, "end": 20 * 60},     # 30 год -> авто
        {"day": "2026-11-21", "court": "PadelHouse · Padel 1", "start": 19 * 60, "end": 21 * 60},  # той самий день, пізніше
        {"day": "2026-11-20", "court": "Fame · Padel hala", "start": 20 * 60, "end": 21 * 60 + 30},  # 8 год -> питати
        {"day": "2026-11-24", "court": "Błonia · Padel 1", "start": 18 * 60, "end": 20 * 60},      # twojtenis, авто
        {"day": "2026-11-25", "court": "PadelHouse · Padel 2", "start": 20 * 60, "end": 23 * 60},  # авто, 2 год
    ]
    state: dict = {}
    quiet = process(blocks, cfg, state, now, tz, sources, {})
    assert [x["day"] for x in booked] == ["2026-11-21", "2026-11-24", "2026-11-25"], booked
    assert booked[0]["court"].startswith("Fame") and booked[0]["end"] - booked[0]["start"] == 120
    assert booked[1]["court"].startswith("Błonia")
    assert booked[2]["start"] == 20 * 60 and booked[2]["end"] == 22 * 60
    assert list(state["booking"]["offers"].values())[0]["end"] == 21 * 60 + 30  # блок 1.5 год -> 1.5 год
    assert quiet == {"2026-11-20", "2026-11-21", "2026-11-24", "2026-11-25"}, quiet
    # повторний запуск нічого нового не робить
    n = len(sent)
    process(blocks, cfg, state, now, tz, sources, {})
    assert len(sent) == n and len(booked) == 3
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
    elif cmd == "probe":  # діагностика запиту бронювання twojtenis (дата в минулому, бронь не створюється)
        _cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        _src = next(s for s in _cfg["sources"] if s.get("type") == "twojtenis")
        _c = Twojtenis(_src)
        _c.login()
        _club = _c._req("GET", f"{_c.api}/Clubs/{_c.club}")
        _loc = next(l for l in _club["locations"] if l.get("type") == _src["court_type"])
        print(f"{_src['short']} probe: {_c.probe(_loc['id'], _loc['name'])}")
    else:
        print(__doc__)
