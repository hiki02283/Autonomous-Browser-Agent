"""
FastMCP Server for Autonomous Browser Agent.
Exposes autonomous browser execution, multi-tab orchestration,
and browser status diagnostics as standard MCP tools.
"""

import os
import sys
import json
import asyncio
from typing import Optional, List, Dict, Any
from fastmcp import FastMCP

from src.config import settings, detect_installed_browsers, resolve_browser_executable
from src.agent import run_autonomous_task
from src.browser_manager import PlaywrightDirectLauncher
from src.multi_tab_manager import MultiTabManager

# Initialize FastMCP Server
mcp = FastMCP(
    name="Autonomous-Browser-Agent",
    instructions="Local autonomous browser controller for non-headless Chrome/Brave automation, multi-tab workflows, and human-in-the-loop tasks."
)


@mcp.tool()
async def run_browser_task(
    prompt: str,
    browser_type: str = "auto",
    headless: bool = False,
    max_steps: int = 30,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
) -> str:
    """
    Execute an autonomous browser automation task using the local Chrome or Brave browser.

    Args:
        prompt: Plain-text natural language instruction (e.g. 'Go to mail.tm, copy temp email, register at example.com').
        browser_type: Browser preference ('auto', 'brave', 'chrome', 'edge', 'chromium').
        headless: Whether to run headlessly. Defaults to False (visible headed mode for human intervention).
        max_steps: Maximum autonomous action steps before stopping.
        llm_provider: Optional LLM provider override ('google', 'openai', 'anthropic').
        llm_model: Optional model name (e.g. 'gemini-2.0-flash', 'gpt-4o-mini').
    """
    try:
        result = await run_autonomous_task(
            task=prompt,
            headless=headless,
            browser_type=browser_type,
            max_steps=max_steps,
            llm_provider=llm_provider,
            model_name=llm_model,
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "hint": "Check that an LLM API key (GOOGLE_API_KEY, GEMINI_API_KEY, or OPENAI_API_KEY) is set in your environment or .env file."
        }, indent=2)


@mcp.tool()
def get_browser_status() -> str:
    """
    Diagnostic tool to inspect available local browsers and current agent configuration.
    Returns detected Chrome/Brave/Edge paths and active settings.
    """
    detected = detect_installed_browsers()
    resolved = resolve_browser_executable()
    google_key_set = bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
    openai_key_set = bool(os.getenv("OPENAI_API_KEY"))
    anthropic_key_set = bool(os.getenv("ANTHROPIC_API_KEY"))

    status = {
        "detected_browsers": detected,
        "resolved_browser": resolved,
        "active_configuration": {
            "default_headless": settings.headless,
            "window_size": f"{settings.window_width}x{settings.window_height}",
            "cdp_url": settings.cdp_url,
            "default_llm_provider": settings.default_llm_provider,
            "default_llm_model": settings.default_llm_model,
        },
        "api_keys_configured": {
            "google_gemini": google_key_set,
            "openai": openai_key_set,
            "anthropic": anthropic_key_set,
        }
    }
    return json.dumps(status, indent=2)


@mcp.tool()
async def multi_tab_quick_flow(
    tab1_url: str,
    tab2_url: str,
    headless: bool = False,
    browser_type: str = "auto",
) -> str:
    """
    Direct multi-tab orchestration helper. Opens tab1 (e.g. mail.tm) and tab2 simultaneously
    in a visible browser window, extracts data, and coordinates between them.

    Args:
        tab1_url: URL for Tab 1 (e.g. 'https://mail.tm')
        tab2_url: URL for Tab 2 (e.g. target registration website)
        headless: Headless flag (defaults to False for headed visible execution)
        browser_type: Browser type to use (auto, brave, chrome, etc.)
    """
    async with PlaywrightDirectLauncher(headless=headless, browser_type=browser_type) as launcher:
        context = launcher.context
        assert context is not None

        # Tab 1
        page1 = await MultiTabManager.open_tab(context, tab1_url)
        page1_title = await page1.title()

        # If mail.tm, attempt temp email extraction
        temp_email = None
        if "mail.tm" in tab1_url:
            try:
                temp_email = await MultiTabManager.extract_temp_email_from_mail_tm(page1, timeout_seconds=10)
            except Exception as e:
                temp_email = f"Extraction error: {e}"

        # Tab 2
        page2 = await MultiTabManager.open_tab(context, tab2_url)
        page2_title = await page2.title()

        # Switch back to Tab 1 to verify switching
        await MultiTabManager.switch_to_tab_by_index(context, 0)
        await asyncio.sleep(1)

        # Switch back to Tab 2
        await MultiTabManager.switch_to_tab_by_index(context, 1)
        await asyncio.sleep(1)

        tabs = await MultiTabManager.get_all_tabs(context)

        return json.dumps({
            "status": "success",
            "tabs_created": [t.to_dict() for t in tabs],
            "tab1": {"url": tab1_url, "title": page1_title, "extracted_email": temp_email},
            "tab2": {"url": tab2_url, "title": page2_title},
        }, indent=2)


def main():
    """Run the FastMCP server with stdio transport."""
    sys.stdout.reconfigure(encoding="utf-8")
    mcp.run()


if __name__ == "__main__":
    main()
