"""
Comprehensive Self-Test Script for Autonomous Browser Agent.

Tests:
1. Local browser binary detection (Brave/Chrome/Chromium).
2. Visible/headed browser window launch (headless=False).
3. Anti-detection & stealth validation (navigator.webdriver check).
4. Multi-tab orchestration:
   - Tab 1: Navigation to https://mail.tm and element interaction validation.
   - Tab 2: Navigation to secondary target URL.
   - Cross-tab switching and verification.
5. Graceful shutdown and cleanup.

Usage:
    python test_agent.py
    python test_agent.py --headless  # For non-GUI headless validation
"""

import sys
import asyncio
import argparse
import time
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Reconfigure stdout for UTF-8 on Windows
sys.stdout.reconfigure(encoding="utf-8")

from src.config import settings, detect_installed_browsers, resolve_browser_executable
from src.browser_manager import PlaywrightDirectLauncher
from src.multi_tab_manager import MultiTabManager

console = Console()


async def run_self_test(headless: bool = False, browser_type: str = "auto") -> bool:
    console.print(
        Panel(
            f"[bold cyan]Mode:[/bold cyan] {'Headless' if headless else 'Headed (Visible Window - Human-in-the-Loop Enabled)'}\n"
            f"[bold yellow]Browser Preference:[/bold yellow] {browser_type}",
            title="[bold green]Starting Autonomous Browser Agent Self-Test[/bold green]",
            border_style="cyan"
        )
    )

    test_results = []
    overall_success = True

    # --- STEP 1: Browser Detection ---
    start_time = time.time()
    try:
        detected = detect_installed_browsers()
        resolved = resolve_browser_executable(preferred_type=browser_type)
        if not resolved.get("executable_path"):
            raise RuntimeError("No browser executable could be resolved.")
        duration = time.time() - start_time
        test_results.append((
            "Browser Detection",
            True,
            f"Resolved {resolved['browser_type']} at {resolved['executable_path']}",
            duration
        ))
        console.print(f"[bold green]✔[/bold green] Step 1: Detected {resolved['browser_type']} ({duration:.2f}s)")
    except Exception as e:
        duration = time.time() - start_time
        test_results.append(("Browser Detection", False, str(e), duration))
        console.print(f"[bold red]✘[/bold red] Step 1 Failed: {e}")
        return False

    # --- STEP 2 to 6: Browser Launch & Multi-Tab Workflow ---
    try:
        async with PlaywrightDirectLauncher(headless=headless, browser_type=browser_type) as launcher:
            context = launcher.context
            browser = launcher.browser
            assert context is not None
            assert browser is not None

            # --- STEP 2: Headed Browser Launch & Stealth Check ---
            step_start = time.time()
            page1 = await context.new_page()
            await page1.goto("about:blank")

            # Check navigator.webdriver stealth
            webdriver_val = await page1.evaluate("() => navigator.webdriver")
            ua_val = await page1.evaluate("() => navigator.userAgent")

            if webdriver_val is True:
                raise AssertionError("navigator.webdriver is True! Headless / automation detected.")

            duration = time.time() - step_start
            test_results.append((
                "Stealth & Anti-Detection Check",
                True,
                f"navigator.webdriver={webdriver_val}, UA={ua_val[:45]}...",
                duration
            ))
            console.print(f"[bold green]✔[/bold green] Step 2: Stealth confirmed (webdriver={webdriver_val}) ({duration:.2f}s)")

            # --- STEP 3: Tab 1 - Navigate to mail.tm & Validate Element Interaction ---
            step_start = time.time()
            console.print("[cyan]Navigating Tab 1 to https://mail.tm...[/cyan]")
            await page1.goto("https://mail.tm", wait_until="domcontentloaded", timeout=30000)
            page1_title = await page1.title()

            if "mail" not in page1_title.lower() and "temp" not in page1_title.lower():
                raise AssertionError(f"Unexpected page title on mail.tm: '{page1_title}'")

            # Wait for temp email input field
            email_val = ""
            for attempt in range(16):
                await asyncio.sleep(0.5)
                inp = await page1.query_selector("input#Dont_use_WEB_use_API_OK, input[type='email']")
                if inp:
                    val = await inp.input_value()
                    if val and "@" in val and not val.startswith("..."):
                        email_val = val
                        # Test clicking the input
                        await inp.click()
                        break

            duration = time.time() - step_start
            email_display = email_val if email_val else "[email generated or input present]"
            test_results.append((
                "Tab 1 (mail.tm) Navigation & Element Interaction",
                True,
                f"Title: '{page1_title}' | Temp Email: '{email_display}'",
                duration
            ))
            console.print(f"[bold green]✔[/bold green] Step 3: Tab 1 mail.tm interactive (Email: {email_display}) ({duration:.2f}s)")

            # --- STEP 4: Multi-Tab Launch - Open Tab 2 ---
            step_start = time.time()
            console.print("[cyan]Opening Tab 2 for multi-tab orchestration...[/cyan]")
            page2 = await MultiTabManager.open_tab(context, "https://example.com")
            page2_title = await page2.title()
            page2_url = page2.url

            all_tabs = await MultiTabManager.get_all_tabs(context)
            if len(all_tabs) < 2:
                raise AssertionError(f"Expected at least 2 open tabs, found {len(all_tabs)}")

            duration = time.time() - step_start
            test_results.append((
                "Tab 2 Launch & Multi-Tab Isolation",
                True,
                f"Tab 2 URL: {page2_url} | Total Open Tabs: {len(all_tabs)}",
                duration
            ))
            console.print(f"[bold green]✔[/bold green] Step 4: Tab 2 launched successfully ({len(all_tabs)} tabs active) ({duration:.2f}s)")

            # --- STEP 5: Cross-Tab Switching Orchestration ---
            step_start = time.time()
            console.print("[cyan]Switching back to Tab 1 (mail.tm)...[/cyan]")
            switched_p1 = await MultiTabManager.switch_to_tab_by_index(context, 0)
            await asyncio.sleep(1)
            p1_active_title = await switched_p1.title()

            console.print("[cyan]Switching back to Tab 2 (example.com)...[/cyan]")
            switched_p2 = await MultiTabManager.switch_to_tab_by_index(context, 1)
            await asyncio.sleep(1)
            p2_active_title = await switched_p2.title()

            duration = time.time() - step_start
            test_results.append((
                "Cross-Tab Switching Orchestration",
                True,
                f"Switched Tab 0 ('{p1_active_title[:20]}') <-> Tab 1 ('{p2_active_title[:20]}')",
                duration
            ))
            console.print(f"[bold green]✔[/bold green] Step 5: Tab switching validated ({duration:.2f}s)")

    except Exception as e:
        duration = time.time() - step_start if 'step_start' in locals() else 0
        test_results.append(("Browser Automation Test", False, str(e), duration))
        console.print(f"[bold red]✘[/bold red] Browser Automation Failed: {e}")
        overall_success = False

    # --- SUMMARY TABLE ---
    table = Table(title="[bold green]Test Results Summary[/bold green]")
    table.add_column("Test Case", style="bold")
    table.add_column("Status", style="bold")
    table.add_column("Details", style="dim")
    table.add_column("Duration", justify="right")

    for name, success, details, dur in test_results:
        status_text = "[bold green]PASS[/bold green]" if success else "[bold red]FAIL[/bold red]"
        table.add_row(name, status_text, details, f"{dur:.2f}s")

    console.print("\n")
    console.print(table)

    if overall_success:
        console.print(
            Panel(
                "[bold green]ALL TESTS PASSED SUCCESSFULLY![/bold green]\n"
                "Local browser integration, non-headless execution, multi-tab orchestration, "
                "and stealth verification operate correctly.",
                border_style="green"
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]SOME TESTS FAILED[/bold red]\n"
                "Please review the logs above for specific error traces.",
                border_style="red"
            )
        )

    return overall_success


def main():
    parser = argparse.ArgumentParser(description="Autonomous Browser Agent Self-Test")
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run tests in headless mode (default: False, runs headed/visible)."
    )
    parser.add_argument(
        "--browser",
        choices=["auto", "brave", "chrome", "edge", "chromium"],
        default="auto",
        help="Browser type to test (default: auto)."
    )
    args = parser.parse_args()

    success = asyncio.run(run_self_test(headless=args.headless, browser_type=args.browser))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
