"""Bám Playwright vào trình duyệt mà GPM Login đã mở (qua CDP)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright


class BrowserSession:
    """Giữ context/page của một profile đã mở sẵn bởi GPM."""

    def __init__(self, pw, browser: Browser, context: BrowserContext, page: Page):
        self._pw = pw
        self.browser = browser
        self.context = context
        self.page = page

    def new_page(self) -> Page:
        self.page = self.context.new_page()
        return self.page

    def minimize(self) -> bool:
        """Thu nhỏ cửa sổ trình duyệt GPM (qua CDP) để chạy ngầm, không nhảy lên màn hình.
        Trả True nếu thành công. Điều khiển vẫn hoạt động khi cửa sổ đã thu nhỏ."""
        try:
            cdp = self.context.new_cdp_session(self.page)
            info = cdp.send("Browser.getWindowForTarget")
            cdp.send("Browser.setWindowBounds",
                     {"windowId": info["windowId"], "bounds": {"windowState": "minimized"}})
            return True
        except Exception:
            return False

    def close(self) -> None:
        # KHÔNG đóng browser (GPM quản lý), chỉ ngắt kết nối Playwright.
        try:
            self.browser.close()
        except Exception:
            pass
        try:
            self._pw.stop()
        except Exception:
            pass


@contextmanager
def connect(cdp_url: str) -> Iterator[BrowserSession]:
    """Kết nối tới trình duyệt qua CDP. Dùng như context manager."""
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
    except Exception:
        # Bám thất bại: PHẢI dừng Playwright, nếu không lần mở profile sau trên cùng luồng sẽ lỗi
        # "Sync API inside the asyncio loop" và cả đợt chạy hỏng theo.
        try:
            pw.stop()
        except Exception:
            pass
        raise
    session = BrowserSession(pw, browser, context, page)
    try:
        yield session
    finally:
        session.close()
