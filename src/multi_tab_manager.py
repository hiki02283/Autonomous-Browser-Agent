"""
Multi-Tab Orchestration Module.
Provides robust support for handling multiple tabs simultaneously, switching between them,
reading background tab data (e.g. mail.tm temp email / OTP codes), and coordinating cross-tab workflows.
"""

import re
import asyncio
from typing import List, Dict, Any, Optional
from playwright.async_api import Page, BrowserContext
from browser_use import BrowserSession


class TabInfo:
    """Represents metadata of an open tab."""
    def __init__(self, index: int, title: str, url: str, target_id: Optional[str] = None):
        self.index = index
        self.title = title
        self.url = url
        self.target_id = target_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "url": self.url,
            "target_id": self.target_id or ""
        }


class MultiTabManager:
    """Orchestrator for managing and querying multiple tabs in Playwright and BrowserSession."""

    @staticmethod
    async def get_all_tabs(context: BrowserContext) -> List[TabInfo]:
        """List all open tabs in a Playwright BrowserContext."""
        pages = context.pages
        tabs = []
        for i, page in enumerate(pages):
            try:
                title = await page.title()
                url = page.url
                tabs.append(TabInfo(index=i, title=title, url=url))
            except Exception:
                tabs.append(TabInfo(index=i, title="[Unresponsive Tab]", url=""))
        return tabs

    @staticmethod
    async def open_tab(context: BrowserContext, url: str = "about:blank") -> Page:
        """Open a new tab in the browser context and navigate to the given URL."""
        page = await context.new_page()
        if url and url != "about:blank":
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return page

    @staticmethod
    async def switch_to_tab_by_index(context: BrowserContext, index: int) -> Page:
        """Bring the tab at the given index to the front."""
        pages = context.pages
        if 0 <= index < len(pages):
            target_page = pages[index]
            await target_page.bring_to_front()
            return target_page
        raise IndexError(f"Tab index {index} out of range (total open tabs: {len(pages)})")

    @staticmethod
    async def switch_to_tab_by_url_pattern(context: BrowserContext, pattern: str) -> Optional[Page]:
        """Find a tab whose URL matches the pattern and bring it to front."""
        regex = re.compile(pattern, re.IGNORECASE)
        for page in context.pages:
            if regex.search(page.url):
                await page.bring_to_front()
                return page
        return None

    @staticmethod
    async def extract_temp_email_from_mail_tm(page: Page, timeout_seconds: int = 15) -> str:
        """
        Extract the generated temporary email address from a mail.tm page.
        Waits for the input element to generate a valid email address.
        """
        # Ensure we are on mail.tm
        if "mail.tm" not in page.url:
            await page.goto("https://mail.tm", wait_until="domcontentloaded")

        selector = "input#Dont_use_WEB_use_API_OK, input[type='email'], input[readonly]"
        await page.wait_for_selector(selector, timeout=timeout_seconds * 1000)

        for _ in range(timeout_seconds * 2):
            input_el = await page.query_selector(selector)
            if input_el:
                val = await input_el.input_value()
                if val and "@" in val and not val.startswith("..."):
                    return val.strip()
            await asyncio.sleep(0.5)

        raise TimeoutError("Timed out waiting for mail.tm to generate an email address.")

    @staticmethod
    async def extract_latest_otp_or_code(page: Page) -> Optional[str]:
        """
        Scan page text, inbox messages, and subject lines for verification / OTP codes.
        Matches common patterns like 4-8 digit numbers or alphanumeric tokens.
        """
        text = await page.inner_text("body")

        # Pattern 1: Explicit labels like "verification code: 123456" or "OTP: 123456"
        labeled_pattern = re.compile(
            r"(?:code|otp|pin|token|verification|password|login)\s*(?:is|:|\-)?\s*([0-9]{4,8})\b",
            re.IGNORECASE
        )
        match = labeled_pattern.search(text)
        if match:
            return match.group(1)

        # Pattern 2: Standalone 6-digit codes
        six_digit_pattern = re.compile(r"\b([0-9]{6})\b")
        six_match = six_digit_pattern.findall(text)
        if six_match:
            return six_match[-1]  # Return latest

        # Pattern 3: Standalone 4-digit codes
        four_digit_pattern = re.compile(r"\b([0-9]{4})\b")
        four_match = four_digit_pattern.findall(text)
        if four_match:
            return four_match[-1]

        return None
