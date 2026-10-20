"""
Streamlit Community Cloud のスリープ対策。

無料枠のアプリは「12時間アクセスが無い」とスリープし、次に開いた人は
「Yes, get this app back up!」を押して30〜40秒待たされる。
そこで GitHub Actions から6時間ごとに本物のブラウザで開く。
  ・寝ていたら起こす
  ・起きていたら、開くだけ（これが「アクセス」として数えられ、タイマーが戻る）

判明していること（2026-10-20 実測）:
  ・単純な HTTP GET や /_stcore/health は、寝ていても 200 を返す → ping では起こせないし、死活監視にもならない
  ・起こすボタンは button[data-testid="wakeup-button-viewer"]
  ・起きた後は iframe[title="streamlitApp"] の中に Streamlit 本体（data-testid="stApp"）が出る

終了コード: 0=アプリが正常に表示された / 1=起動できなかった / 2=起きたがアプリがエラー表示
失敗すると GitHub Actions のジョブが赤くなり、リポジトリ所有者にメールが飛ぶ。
"""

import os
import sys
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

APP_URL = os.environ.get(
    "APP_URL", "https://usami-castle-wrsqbm5zoqhfcpme8fctc4.streamlit.app/"
)

WAKE_BUTTON = '[data-testid="wakeup-button-viewer"]'
APP_IFRAME = 'iframe[title="streamlitApp"]'

FIRST_PAINT_MS = 60_000     # 開いてから「起きている or 寝ている」が判別できるまで
WAKE_UP_MS = 240_000        # 起こしてからアプリが出るまで（実測30〜40秒。余裕を持たせる）
HOLD_SECONDS = 20           # アプリを表示したまま保持する時間（セッションとして数えさせる）


def log(message):
    print(f"[keepalive] {message}", flush=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        log(f"open {APP_URL}")
        page.goto(APP_URL, wait_until="domcontentloaded", timeout=FIRST_PAINT_MS)

        # 寝ているなら起こすボタン、起きているならアプリの iframe が先に出る。
        page.locator(f"{WAKE_BUTTON}, {APP_IFRAME}").first.wait_for(
            state="attached", timeout=FIRST_PAINT_MS
        )

        was_asleep = page.locator(WAKE_BUTTON).count() > 0
        log(f"was_asleep={was_asleep}")

        if was_asleep:
            page.locator(WAKE_BUTTON).click()
            started = time.time()
            log("clicked wake button")

        try:
            page.locator(APP_IFRAME).wait_for(state="attached", timeout=WAKE_UP_MS)
            app = page.frame_locator(APP_IFRAME)
            app.locator('[data-testid="stApp"]').wait_for(
                state="visible", timeout=WAKE_UP_MS
            )
        except PlaywrightTimeout:
            log("FAILED: アプリの本体が表示されなかった")
            browser.close()
            return 1

        if was_asleep:
            log(f"woke up in {time.time() - started:.0f}s")

        # アプリは起きたがコード側のエラーで落ちている、を見逃さない。
        time.sleep(3)
        if app.locator('[data-testid="stException"]').count() > 0:
            log("FAILED: アプリは起きたが、画面に例外が表示されている")
            browser.close()
            return 2

        time.sleep(HOLD_SECONDS)
        log("OK: アプリは正常に表示されている")
        browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
