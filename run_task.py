"""
Standalone CLI Interface for Autonomous Browser Agent.
Accepts plain-text prompts and executes the autonomous loop in headed (or headless) mode.

Usage:
    python run_task.py "Go to mail.tm, copy the temp email, and sign up at example.com"
    python run_task.py --task "Search Google for Playwright tips" --browser brave
    python run_task.py --status
"""

import sys
import os
import argparse
import asyncio
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Reconfigure stdout for UTF-8 on Windows
sys.stdout.reconfigure(encoding="utf-8")

from src.config import settings, detect_installed_browsers, resolve_browser_executable
from src.agent import run_autonomous_task

console = Console()


def show_browser_status():
    """Display detected browsers and configuration table."""
    detected = detect_installed_browsers()
    resolved = resolve_browser_executable()

    table = Table(title="[bold cyan]Detected Local Browsers & Status[/bold cyan]")
    table.add_column("Browser", style="bold")
    table.add_column("Status", style="yellow")
    table.add_column("Path", style="dim")

    for name, path in detected.items():
        status = "[bold green]Available[/bold green]" if path else "[red]Not Found[/red]"
        table.add_row(name.capitalize(), status, path or "-")

    console.print(table)
    console.print(f"[bold green]Default Resolved Browser:[/bold green] {resolved['browser_type']} ({resolved['executable_path']})")
    console.print(f"[bold cyan]Default Execution Mode:[/bold cyan] {'Headless' if settings.headless else 'Headed (Visible GUI)'}")
    
    # Check keys
    google_key = bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
    openai_key = bool(os.getenv("OPENAI_API_KEY"))
    anthropic_key = bool(os.getenv("ANTHROPIC_API_KEY"))
    console.print(f"[bold yellow]Configured API Keys:[/bold yellow] Google/Gemini: {google_key}, OpenAI: {openai_key}, Anthropic: {anthropic_key}")


async def async_main():
    parser = argparse.ArgumentParser(
        description="Autonomous Browser Agent CLI - Headed/visible local browser automation."
    )
    parser.add_argument(
        "task",
        nargs="?",
        default=None,
        help="Plain-text task prompt (e.g. 'Go to mail.tm and read the latest email')."
    )
    parser.add_argument(
        "-t", "--task-text",
        dest="flag_task",
        default=None,
        help="Alternative flag for task prompt."
    )
    parser.add_argument(
        "--browser",
        choices=["auto", "brave", "chrome", "edge", "chromium"],
        default="auto",
        help="Browser to launch (default: auto, prioritizing Brave -> Chrome -> Edge -> Chromium)."
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run in headless mode (default: False, runs headed/visible for human intervention)."
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum autonomous steps allowed (default: 30)."
    )
    parser.add_argument(
        "--model",
        default=None,
        help="LLM model name (e.g. 'gemini-2.0-flash', 'gpt-4o-mini')."
    )
    parser.add_argument(
        "--provider",
        choices=["google", "openai", "anthropic"],
        default=None,
        help="LLM provider (default: google or inferred from available API keys)."
    )
    parser.add_argument(
        "--cdp-url",
        default=None,
        help="Attach to an already running browser instance via CDP URL (e.g. http://localhost:9222)."
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Display detected browsers and configuration then exit."
    )

    args = parser.parse_args()

    if args.status:
        show_browser_status()
        return

    prompt = args.task or args.flag_task
    if not prompt:
        console.print("[bold yellow]No task specified.[/bold yellow] Showing status overview:")
        show_browser_status()
        console.print("\n[dim]Usage: python run_task.py \"Your task prompt here\"[/dim]")
        return

    # Display execution banner
    resolved = resolve_browser_executable(preferred_type=args.browser)
    console.print(
        Panel(
            f"[bold cyan]Task:[/bold cyan] {prompt}\n"
            f"[bold yellow]Browser:[/bold yellow] {resolved['browser_type']} ({resolved['executable_path'] or 'Playwright default'})\n"
            f"[bold green]Mode:[/bold green] {'Headless' if args.headless else 'Headed (Visible Window - Human-in-the-Loop Enabled)'}\n"
            f"[bold magenta]Max Steps:[/bold magenta] {args.max_steps}",
            title="[bold green]Autonomous Browser Agent[/bold green]",
            border_style="cyan"
        )
    )

    console.print("[cyan]Starting autonomous loop...[/cyan]")
    try:
        result = await run_autonomous_task(
            task=prompt,
            headless=args.headless,
            browser_type=args.browser,
            max_steps=args.max_steps,
            model_name=args.model,
            llm_provider=args.provider,
            cdp_url=args.cdp_url,
        )

        console.print(
            Panel(
                f"[bold green]Execution Succeeded![/bold green]\n\n"
                f"[bold]Result Summary:[/bold]\n{result['final_result']}\n\n"
                f"[dim]Total Steps: {result['total_steps']} | URLs: {result['urls_visited']}[/dim]",
                title="[bold green]Task Complete[/bold green]",
                border_style="green"
            )
        )
    except Exception as e:
        console.print(
            Panel(
                f"[bold red]Task Execution Error:[/bold red]\n{str(e)}\n\n"
                f"[yellow]Troubleshooting Tips:[/yellow]\n"
                f"1. Make sure GOOGLE_API_KEY (or GEMINI_API_KEY) or OPENAI_API_KEY is configured in .env\n"
                f"2. For manual intervention (CAPTCHAs/2FA), keep headless=False (default)\n"
                f"3. Run 'python test_agent.py' to verify your browser environment.",
                title="[bold red]Execution Failed[/bold red]",
                border_style="red"
            )
        )


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
