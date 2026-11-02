"""Скриншоты всех страниц: 1440 px светлая и тёмная тема, планшет 1024 px, телефон 400 px.

Запуск (приложение уже работает на порту 8517):  python scripts/screenshots.py [папка] [страницы…]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8517"
PAGES = ["", "heatmap", "register", "card", "dynamics", "owners", "import", "journal", "method"]
MODES = [("light", 1440, 1000, "light"), ("dark", 1440, 1000, "dark"), ("tablet", 1024, 1366, "light"), ("phone", 400, 860, "light")]


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "screens")
    pages = sys.argv[2:] or PAGES
    out.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, w, h, scheme in MODES:
            ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, locale="ru-RU")
            page = ctx.new_page()
            for slug in pages:
                page.goto(f"{BASE}/{slug}")
                page.wait_for_selector("h1", timeout=60000)
                page.wait_for_timeout(2500)  # графики Plotly дорисовываются после заголовка
                page.screenshot(path=str(out / f"{slug or 'overview'}_{name}.png"), full_page=True)
                print("ok", slug, name)
            ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
