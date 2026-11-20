#!/usr/bin/env python3
"""Вхід у twojtenis.pl через невидимий браузер (Playwright + Chromium).

twojtenis використовує Auth0 і не дозволяє входити паролем без сторінки входу,
тому бот відкриває звичайну сторінку входу, вводить TWOJTENIS_LOGIN / TWOJTENIS_PASSWORD
і забирає токен (діє 24 год). Якщо Auth0 покаже перевірку «я не робот», вхід не вдасться:
обходити її бот не намагається.

  python tt_login.py check   - перевірити вхід (друкує лише результат, без токена)
"""
from __future__ import annotations

import json
import os
import sys
import time

APP_URL = "https://app.twojtenis.pl/"
TOKEN_KEY_PART = "api.twojetenis.pl"


class LoginError(RuntimeError):
    pass


def get_token(timeout_s: int = 90) -> tuple[str, int]:
    """Повертає (access_token, expiresAt epoch). Кидає LoginError."""
    user = os.environ.get("TWOJTENIS_LOGIN", "").strip()
    pw = os.environ.get("TWOJTENIS_PASSWORD", "")
    if not user or not pw:
        raise LoginError("немає секретів TWOJTENIS_LOGIN / TWOJTENIS_PASSWORD")
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        raise LoginError("не встановлено playwright") from None

    deadline = time.time() + timeout_s
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(locale="pl-PL")
            page.goto(APP_URL, wait_until="domcontentloaded", timeout=60000)
            # кнопка «Zaloguj się» у застосунку -> перехід на сторінку Auth0
            btn = page.get_by_role("button", name="Zaloguj").or_(page.get_by_text("Zaloguj się")).first
            btn.wait_for(timeout=30000)
            btn.click()
            page.wait_for_url("**auth0.com/**", timeout=30000)

            def page_has_captcha() -> bool:
                html = page.content().lower()
                return "captcha" in html and ("g-recaptcha" in html or "hcaptcha" in html or "arkose" in html
                                              or 'name="captcha"' in html)

            # Universal Login: email і пароль на одній сторінці або по черзі
            email = page.locator("input[name=username], input[name=email], input[type=email]").first
            email.wait_for(timeout=20000)
            email.fill(user)
            pwd = page.locator("input[name=password], input[type=password]").first
            if not pwd.is_visible():
                page.locator("button[type=submit]").first.click()
                pwd.wait_for(timeout=20000)
            pwd.fill(pw)
            if page_has_captcha():
                raise LoginError("Auth0 показав перевірку «я не робот»")
            page.locator("button[type=submit], button[name=action]").first.click()

            # чекаємо повернення в застосунок і токена в localStorage
            while time.time() < deadline:
                if "app.twojtenis.pl" in page.url:
                    raw = page.evaluate(
                        "k => { const key = Object.keys(localStorage).find(x => x.includes(k));"
                        " return key ? localStorage.getItem(key) : null; }", TOKEN_KEY_PART)
                    if raw:
                        data = json.loads(raw)
                        token = (data.get("body") or {}).get("access_token")
                        if token:
                            return token, int(data.get("expiresAt") or 0)
                elif "auth0.com" in page.url:
                    if page_has_captcha():
                        raise LoginError("Auth0 показав перевірку «я не робот»")
                    err = page.locator("[id*=error], .ulp-input-error-message, .ulp-error-info, [role=alert]")
                    if err.count() and err.first.is_visible():
                        msg = err.first.inner_text().strip()[:200]
                        if msg:
                            raise LoginError(f"Auth0: {msg}")
                page.wait_for_timeout(1000)
            raise LoginError(f"не дочекався входу (сторінка: {page.url.split('?')[0]})")
        except PWTimeout as exc:
            raise LoginError(f"сторінка входу не відповіла: {str(exc).splitlines()[0][:150]}") from None
        finally:
            browser.close()


if __name__ == "__main__":
    if (sys.argv[1:] or [""])[0] == "check":
        try:
            _, exp = get_token()
        except LoginError as exc:
            print(f"Błonia: ПОМИЛКА входу через браузер: {exc}")
            sys.exit(1)
        print(f"Błonia: вхід через браузер OK, токен діє до {time.strftime('%Y-%m-%d %H:%M', time.gmtime(exp))} UTC")
    else:
        print(__doc__)
