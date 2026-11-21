#!/usr/bin/env python3
"""Додавання подій у календар iCloud через CalDAV (стандартний протокол, як у Thunderbird).

Секрети в GitHub:
  ICLOUD_USER          - Apple ID (email)
  ICLOUD_APP_PASSWORD  - пароль для застосунків, створений на appleid.apple.com (не основний пароль!)
  ICLOUD_CALENDAR      - назва календаря, як на iPhone (наприклад, спільний з дівчиною)

  python icloud_cal.py check   - перевірити доступ і чи знайдено календар (назви календарів у Telegram, не в журналі)
"""
from __future__ import annotations

import base64
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT_URL = "https://caldav.icloud.com/"
NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}


class CalError(RuntimeError):
    pass


def configured() -> bool:
    return all(os.environ.get(k, "").strip() for k in ("ICLOUD_USER", "ICLOUD_APP_PASSWORD", "ICLOUD_CALENDAR"))


def _auth() -> str:
    user = os.environ.get("ICLOUD_USER", "").strip()
    pw = os.environ.get("ICLOUD_APP_PASSWORD", "").strip()
    if not user or not pw:
        raise CalError("немає секретів ICLOUD_USER / ICLOUD_APP_PASSWORD")
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def _req(method: str, url: str, body: str | bytes | None = None, headers: dict | None = None) -> tuple[int, str, str]:
    h = {"Authorization": _auth(), "User-Agent": "padel-slot-monitor/1.0"}
    h.update(headers or {})
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), resp.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise CalError("iCloud не прийняв логін або пароль для застосунків (HTTP 401)") from None
        return exc.code, exc.read().decode("utf-8", "replace"), url


def _propfind(url: str, props: str, depth: str = "0") -> tuple[ET.Element, str]:
    body = f'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop>{props}</d:prop></d:propfind>'
    code, text, final = _req("PROPFIND", url, body, {"Depth": depth, "Content-Type": "application/xml; charset=utf-8"})
    if code not in (200, 207):
        raise CalError(f"PROPFIND HTTP {code}")
    return ET.fromstring(text), final


def _href(root: ET.Element, prop: str) -> str:
    el = root.find(f".//{prop}/d:href", NS)
    if el is None or not (el.text or "").strip():
        raise CalError(f"сервер не повернув {prop}")
    return el.text.strip()


def list_calendars() -> list[tuple[str, str]]:
    """[(назва, url)] усіх календарів, у які можна писати події."""
    root, final = _propfind(ROOT_URL, "<d:current-user-principal/>")
    principal = urllib.parse.urljoin(final, _href(root, "d:current-user-principal"))
    root, final = _propfind(principal, "<c:calendar-home-set/>")
    home = urllib.parse.urljoin(final, _href(root, "c:calendar-home-set"))
    root, final = _propfind(home, "<d:displayname/><d:resourcetype/><c:supported-calendar-component-set/>", "1")
    out = []
    for resp in root.findall("d:response", NS):
        if resp.find(".//d:resourcetype/c:calendar", NS) is None:
            continue
        comps = [c.get("name") for c in resp.findall(".//c:supported-calendar-component-set/c:comp", NS)]
        if comps and "VEVENT" not in comps:
            continue  # нагадування (VTODO), не календар подій
        name = (resp.findtext(".//d:displayname", default="", namespaces=NS) or "").strip()
        href = resp.findtext("d:href", default="", namespaces=NS).strip()
        out.append((name, urllib.parse.urljoin(final, href)))
    return out


def find_calendar(name: str | None = None) -> str:
    name = (name or os.environ.get("ICLOUD_CALENDAR", "")).strip()
    cals = list_calendars()
    for n, url in cals:
        if n.casefold() == name.casefold():
            return url
    raise CalError(f"календар «{name}» не знайдено (є {len(cals)} календарів)")


def _res_url(cal_url: str, uid: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", uid)
    return cal_url.rstrip("/") + f"/{safe}.ics"


def put_event(uid: str, ics: bytes, cal_url: str | None = None) -> None:
    cal_url = cal_url or find_calendar()
    code, text, _ = _req("PUT", _res_url(cal_url, uid), ics, {"Content-Type": "text/calendar; charset=utf-8"})
    if code not in (200, 201, 204):
        raise CalError(f"не вдалося додати подію: HTTP {code} {text[:150]}")


def delete_event(uid: str, cal_url: str | None = None) -> None:
    cal_url = cal_url or find_calendar()
    code, text, _ = _req("DELETE", _res_url(cal_url, uid))
    if code not in (200, 204, 404):
        raise CalError(f"не вдалося видалити подію: HTTP {code} {text[:150]}")


if __name__ == "__main__":
    if (sys.argv[1:] or [""])[0] == "check":
        try:
            cals = list_calendars()
        except CalError as exc:
            print(f"iCloud: ПОМИЛКА {exc}")
            sys.exit(1)
        target = os.environ.get("ICLOUD_CALENDAR", "").strip()
        found = any(n.casefold() == target.casefold() for n, _ in cals)
        print(f"iCloud: вхід OK, календарів подій: {len(cals)}; потрібний календар {'знайдено' if found else 'НЕ знайдено'}")
        if not found:
            import booker  # назви календарів надсилаємо в Telegram, а не в публічний журнал
            booker.tg_send("📅 Календар «" + target + "» не знайдено. Є такі календарі:\n"
                           + "\n".join(f"• {n}" for n, _ in cals) + "\nВпишіть точну назву в секрет ICLOUD_CALENDAR.")
            sys.exit(1)
    else:
        print(__doc__)
