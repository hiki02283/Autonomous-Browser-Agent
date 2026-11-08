"""
Custom Browser Controller Module.
Extends browser-use Controller with multi-tab helpers, temporary email support,
OTP extraction, and Human-in-the-Loop intervention actions.
"""

import re
import asyncio
from typing import Optional, List, Dict, Any, Callable, Awaitable
from rich.console import Console
from rich.panel import Panel

from browser_use import Controller, BrowserSession
from browser_use.browser.events import SwitchTabEvent, CloseTabEvent

console = Console()


def create_agent_controller(
    on_human_intervention: Optional[Callable[[str, int], Awaitable[None]]] = None,
    resume_event: Optional[asyncio.Event] = None,
) -> Controller:
    """
    Instantiate and configure the Controller with multi-tab and human-in-the-loop actions.
    Supports real-time callback and resume events for GUI and CLI integration.
    """
    controller = Controller()

    @controller.action(
        "Open a new browser tab with the specified URL and switch focus to it."
    )
    async def open_new_tab(url: str, browser_session: BrowserSession) -> str:
        """Create a new page/tab and navigate to the given URL."""
        try:
            page = await browser_session.new_page(url=url)
            target_id = page.target_id
            event = browser_session.event_bus.dispatch(SwitchTabEvent(target_id=target_id))
            await event
            return f"Successfully opened new tab for '{url}' (target_id: {target_id[-4:]})"
        except Exception as e:
            return f"Failed to open new tab: {str(e)}"

    @controller.action(
        "List all currently open tabs with their 4-character Tab IDs, titles, and URLs."
    )
    async def list_open_tabs(browser_session: BrowserSession) -> str:
        """Retrieve and format a list of all active browser tabs."""
        try:
            tabs = await browser_session.get_tabs()
            if not tabs:
                return "No open tabs found."
            lines = ["Open Tabs:"]
            for i, tab in enumerate(tabs):
                tab_id_short = tab.target_id[-4:] if tab.target_id else f"tab_{i}"
                lines.append(f"  [{tab_id_short}] {tab.title} - {tab.url}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error retrieving tabs: {str(e)}"

    @controller.action(
        "Switch to an existing tab by searching for a keyword in its URL or title (e.g. 'mail.tm' or 'signup')."
    )
    async def switch_tab_by_keyword(keyword: str, browser_session: BrowserSession) -> str:
        """Find a tab containing the keyword in its URL or title and switch to it."""
        try:
            tabs = await browser_session.get_tabs()
            kw_lower = keyword.lower()
            for tab in tabs:
                if kw_lower in (tab.url or "").lower() or kw_lower in (tab.title or "").lower():
                    event = browser_session.event_bus.dispatch(SwitchTabEvent(target_id=tab.target_id))
                    await event
                    return f"Switched to tab: '{tab.title}' ({tab.url})"
            return f"No open tab found matching '{keyword}'. Open tabs: {[t.url for t in tabs]}"
        except Exception as e:
            return f"Failed to switch tab: {str(e)}"

    @controller.action(
        "Extract the generated temporary email address from mail.tm. Works if mail.tm is active or opens it in a new tab."
    )
    async def get_mail_tm_email(browser_session: BrowserSession) -> str:
        """Find mail.tm tab or open one, and extract the generated temporary email address."""
        try:
            tabs = await browser_session.get_tabs()
            mail_tab = None
            for tab in tabs:
                if "mail.tm" in (tab.url or "").lower():
                    mail_tab = tab
                    break

            if not mail_tab:
                page = await browser_session.new_page(url="https://mail.tm")
                target_id = page.target_id
                event = browser_session.event_bus.dispatch(SwitchTabEvent(target_id=target_id))
                await event
            else:
                event = browser_session.event_bus.dispatch(SwitchTabEvent(target_id=mail_tab.target_id))
                await event

            await asyncio.sleep(4)

            state = await browser_session.get_browser_state_summary()
            text_content = state.dom_state.text_content if hasattr(state, "dom_state") else ""

            email_match = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", str(text_content))
            if email_match:
                return f"Temporary email address: {email_match.group(1)}"

            return "Opened mail.tm. Please check the email input on the active tab."
        except Exception as e:
            return f"Error obtaining temp email: {str(e)}"

    @controller.action(
        "Pause agent and request human assistance for CAPTCHA, 2FA, SMS code, or OTP verification in the headed browser."
    )
    async def request_human_help(reason: str, timeout_seconds: int = 60) -> str:
        """
        Request manual human intervention. Alerts the user in both the terminal and GUI
        so they can solve the challenge directly in the visible browser window.
        """
        console.print(
            Panel(
                f"[bold red]HUMAN INTERVENTION REQUIRED[/bold red]\n\n"
                f"[yellow]Reason:[/yellow] {reason}\n"
                f"[cyan]Action:[/cyan] The browser window is visible. Please complete the manual step (e.g. solve CAPTCHA, enter 2FA).\n"
                f"[green]Waiting up to {timeout_seconds} seconds (or click 'Continue' in GUI)...[/green]",
                title="[bold yellow]Human-in-the-Loop[/bold yellow]",
                border_style="red"
            )
        )

        if on_human_intervention:
            try:
                await on_human_intervention(reason, timeout_seconds)
            except Exception:
                pass

        if resume_event:
            try:
                # Wait for user click in GUI or timeout
                await asyncio.wait_for(resume_event.wait(), timeout=float(timeout_seconds))
                resume_event.clear()
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(min(timeout_seconds, 15))

        return f"Human assistance window completed for reason: '{reason}'. Resuming agent actions."

    return controller
