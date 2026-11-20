"""Optional end-to-end UI check for the API Sentinel dashboard.

Requires a running server and Playwright + Chromium:

    MONITOR_TOKEN=secret python app.py &
    python -m pip install playwright && python -m playwright install chromium
    MONITOR_TOKEN=secret BASE_URL=http://localhost:5000 python tests/browser_ui_check.py

It drives the real UI against real backend data: sorting, density, mute,
the details inspector, a real manual probe (via Run All Health Checks),
CSV export, monitor create + delete, and the mobile layout.
"""
import os
import sys

from playwright.sync_api import expect, sync_playwright

BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')
TOKEN = os.environ.get('MONITOR_TOKEN', '')
SHOTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs')
# Fixed/sticky chrome is flattened for full-page captures so nothing is painted twice.
DESKTOP_SHOT_STYLE = """.rail{position:absolute!important;top:0!important;bottom:auto!important;height:100%!important}
.top{position:static!important}.inspector{position:static!important}#toast{display:none!important}"""
MOBILE_SHOT_STYLE = """.rail{position:relative!important;height:auto!important}
.top{position:static!important}.inspector{position:static!important}#toast{display:none!important}"""


def unlock(page):
    page.get_by_label('Operator token').fill(TOKEN)
    page.get_by_role('button', name='Unlock editing').click()


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        errors = []
        page = browser.new_page(viewport={'width': 1440, 'height': 980}, accept_downloads=True)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(BASE_URL)

        # Live charts come from real recorded checks.
        expect(page.locator('#trend .scatterlayer')).to_be_visible(timeout=20000)
        expect(page.locator('#compare .main-svg').first).to_be_visible(timeout=5000)

        # Button effects: ripple appears on pointer down.
        page.locator('#add').dispatch_event('pointerdown', {'clientX': 12, 'clientY': 12})
        expect(page.locator('#add .ripple')).to_have_count(1, timeout=3000)

        # Table controls.
        page.locator('[data-sort="name"]').click()
        expect(page.locator('th[aria-sort]')).to_have_attribute('aria-sort', 'ascending')
        page.locator('#mute').click()
        expect(page.locator('#mute')).to_have_attribute('aria-pressed', 'true')
        page.locator('#mute').click()
        page.locator('#density').click()
        expect(page.locator('#split')).to_have_class('split roomy')
        page.locator('#density').click()
        expect(page.locator('#split')).to_have_class('split')

        # Details inspector.
        page.locator('#endpoints tr').first.get_by_role('button', name='Details').click()
        expect(page.locator('#inspector svg[role="img"]')).to_be_visible()
        expect(page.locator('#inspector')).to_contain_text('Check-based uptime')
        page.locator('#close-inspector').click()
        expect(page.locator('#inspector')).to_be_hidden()

        # Real manual probe via Run All Health Checks with an in-app confirm dialog.
        page.locator('#endpoints tr').first.locator('[data-select]').check()
        expect(page.locator('#selection')).to_have_text('1 selected')
        page.locator('#run-all').click()
        unlock(page)
        expect(page.locator('#confirm-title')).to_have_text('Run health checks now?')
        page.locator('#confirm-ok').click()
        expect(page.locator('#toast')).to_contain_text('passed', timeout=30000)

        # Search filter.
        page.locator('#search').fill('HTTPBin')
        expect(page.locator('#endpoints tr')).to_have_count(1)
        page.locator('#search').fill('')
        page.locator('[data-select]').first.uncheck()

        # CSV export.
        with page.expect_download() as download:
            page.locator('#export').click()
        assert 'timestamp_utc' in open(download.value.path(), encoding='utf-8').read()

        # CRUD: add a monitor, then delete it again (token already unlocked).
        page.locator('#new-monitor').click()
        expect(page.locator('#editor')).to_be_visible()
        page.locator('#editor input[name="name"]').fill('UI Check Endpoint')
        page.locator('#editor input[name="url"]').fill('https://httpbin.org/status/204')
        page.locator('#editor textarea[name="description"]').fill('Temporary endpoint created by the browser check.')
        page.locator('#editor input[name="interval"]').fill('120')
        page.locator('#editor input[name="threshold"]').fill('800')
        page.get_by_role('button', name='Save monitor').click()
        expect(page.locator('#toast')).to_contain_text('Monitor added', timeout=15000)
        page.locator('#search').fill('UI Check Endpoint')
        row = page.locator('#endpoints tr').first
        expect(row).to_contain_text('UI Check Endpoint')
        row.get_by_role('button', name='Details').click()
        page.locator('#inspector').get_by_role('button', name='Delete').click()
        expect(page.locator('#confirm-title')).to_have_text('Delete monitor?')
        page.locator('#confirm-ok').click()
        expect(page.locator('#toast')).to_contain_text('Monitor deleted', timeout=15000)
        page.locator('#search').fill('')

        # Screenshot with the inspector open for documentation.
        page.locator('#endpoints tr').first.get_by_role('button', name='Details').click()
        page.add_style_tag(content=DESKTOP_SHOT_STYLE)
        page.evaluate("document.querySelector('.rail').style.height = document.documentElement.scrollHeight + 'px'")
        page.screenshot(path=os.path.join(SHOTS, 'dashboard.png'), full_page=True)

        # Mobile layout: no horizontal page scroll, stacked navigation.
        mobile = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        mobile.goto(BASE_URL)
        expect(mobile.locator('#trend .scatterlayer')).to_be_visible(timeout=20000)
        assert mobile.evaluate('document.documentElement.scrollWidth === innerWidth')
        assert mobile.evaluate("getComputedStyle(document.querySelector('.rail')).position === 'relative'")
        mobile.locator('#endpoints tr').first.get_by_role('button', name='Details').click()
        expect(mobile.locator('#inspector svg[role="img"]')).to_be_visible()
        mobile.add_style_tag(content=MOBILE_SHOT_STYLE)
        mobile.screenshot(path=os.path.join(SHOTS, 'dashboard-mobile.png'), full_page=True)

        browser.close()
        assert not errors, errors
    print('API Sentinel UI verified')


if __name__ == '__main__':
    sys.exit(main())
