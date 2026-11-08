"""
Browser Manager Module.
Configures and initializes browser-use BrowserSession and Playwright instances
with anti-detection flags, visible/headed mode, custom executable paths, and multi-tab orchestration.
"""

import os
import sys
import asyncio
from typing import Optional, Dict, Any, List
from pathlib import Path

from browser_use import BrowserSession, BrowserProfile
from playwright.async_api import async_playwright, Browser as PlaywrightBrowser, BrowserContext as PlaywrightContext

from src.config import settings, resolve_browser_executable


def get_anti_detection_args(window_width: int = 1280, window_height: int = 900) -> List[str]:
    """Return standard Chromium flags to disable automation banners and enhance stealth."""
    return [
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check",
        "--disable-infobars",
        "--no-first-run",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        f"--window-size={window_width},{window_height}",
    ]


def create_browser_profile(
    headless: Optional[bool] = None,
    browser_type: Optional[str] = None,
    custom_executable_path: Optional[str] = None,
    cdp_url: Optional[str] = None,
    window_width: Optional[int] = None,
    window_height: Optional[int] = None,
    user_data_dir: Optional[str] = None,
) -> BrowserProfile:
    """
    Construct a configured BrowserProfile for browser-use.
    By default runs headed (headless=False) using the detected local browser (e.g. Brave/Chrome).
    """
    is_headless = headless if headless is not None else settings.headless
    w_width = window_width or settings.window_width
    w_height = window_height or settings.window_height
    active_cdp = cdp_url or settings.cdp_url

    resolved = resolve_browser_executable(
        preferred_type=browser_type or settings.browser_type,
        custom_path=custom_executable_path or settings.browser_executable_path
    )
    exe_path = resolved["executable_path"] or None

    flags = get_anti_detection_args(window_width=w_width, window_height=w_height)

    profile_kwargs: Dict[str, Any] = {
        "headless": is_headless,
        "is_local": True if not active_cdp else False,
        "window_size": {"width": w_width, "height": w_height},
        "args": flags,
        "enable_default_extensions": False,  # Clean startup without downloading extra assets
    }

    if active_cdp:
        profile_kwargs["cdp_url"] = active_cdp
    elif exe_path:
        profile_kwargs["executable_path"] = exe_path

    if user_data_dir:
        profile_kwargs["user_data_dir"] = user_data_dir

    return BrowserProfile(**profile_kwargs)


def create_browser_session(
    headless: Optional[bool] = None,
    browser_type: Optional[str] = None,
    custom_executable_path: Optional[str] = None,
    cdp_url: Optional[str] = None,
    window_width: Optional[int] = None,
    window_height: Optional[int] = None,
) -> BrowserSession:
    """Instantiate a BrowserSession with optimal parameters for human-in-the-loop and multi-tab usage."""
    profile = create_browser_profile(
        headless=headless,
        browser_type=browser_type,
        custom_executable_path=custom_executable_path,
        cdp_url=cdp_url,
        window_width=window_width,
        window_height=window_height,
    )
    return BrowserSession(browser_profile=profile)


class PlaywrightDirectLauncher:
    """
    Direct Playwright context manager for low-level automation, self-tests,
    and high-performance direct page scripting.
    """
    def __init__(
        self,
        headless: Optional[bool] = None,
        browser_type: Optional[str] = None,
        custom_executable_path: Optional[str] = None,
        window_width: int = 1280,
        window_height: int = 900,
    ):
        self.headless = headless if headless is not None else settings.headless
        self.resolved = resolve_browser_executable(
            preferred_type=browser_type or settings.browser_type,
            custom_path=custom_executable_path or settings.browser_executable_path
        )
        self.window_width = window_width
        self.window_height = window_height
        self._pw = None
        self.browser: Optional[PlaywrightBrowser] = None
        self.context: Optional[PlaywrightContext] = None

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        exe_path = self.resolved["executable_path"] or None
        launch_args = get_anti_detection_args(self.window_width, self.window_height)

        launch_kwargs: Dict[str, Any] = {
            "headless": self.headless,
            "args": launch_args,
        }
        if exe_path:
            launch_kwargs["executable_path"] = exe_path

        self.browser = await self._pw.chromium.launch(**launch_kwargs)
        self.context = await self.browser.new_context(
            viewport={"width": self.window_width, "height": self.window_height},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        # Apply stealth script to override navigator.webdriver
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()
