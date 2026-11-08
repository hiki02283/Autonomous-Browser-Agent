"""
Configuration and Browser Detection Module.
Detects installed browsers (Chrome, Brave, Edge, Playwright Chromium) and provides unified settings.
"""

import os
import sys
import platform
from pathlib import Path
from typing import Dict, Optional, List
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load environment variables
load_dotenv()

# Synchronize GEMINI_API_KEY and GOOGLE_API_KEY if one is set
if os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.getenv("GEMINI_API_KEY", "")
elif os.getenv("GOOGLE_API_KEY") and not os.getenv("GEMINI_API_KEY"):
    os.environ["GEMINI_API_KEY"] = os.getenv("GOOGLE_API_KEY", "")


def get_default_browser_candidates() -> Dict[str, List[Path]]:
    """Return common installation paths for major browsers based on operating system."""
    system = platform.system()
    candidates: Dict[str, List[Path]] = {
        "brave": [],
        "chrome": [],
        "edge": [],
        "chromium": []
    }

    if system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")

        # Brave paths
        candidates["brave"] = [
            Path(program_files) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            Path(program_files_x86) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            Path(local_app_data) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        ]

        # Chrome paths
        candidates["chrome"] = [
            Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]

        # Edge paths
        candidates["edge"] = [
            Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        ]

        # Playwright Chromium fallback directory
        ms_playwright = Path(local_app_data) / "ms-playwright"
        if ms_playwright.exists():
            for chrome_exe in ms_playwright.glob("chromium-*/chrome-win/chrome.exe"):
                candidates["chromium"].append(chrome_exe)
            for chrome_exe in ms_playwright.glob("chromium-*/chrome-win64/chrome.exe"):
                candidates["chromium"].append(chrome_exe)

    elif system == "Darwin":  # macOS
        candidates["brave"] = [
            Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")
        ]
        candidates["chrome"] = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        ]
        candidates["edge"] = [
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
        ]
    else:  # Linux
        candidates["brave"] = [
            Path("/usr/bin/brave-browser"),
            Path("/usr/bin/brave"),
            Path("/snap/bin/brave")
        ]
        candidates["chrome"] = [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium-browser"),
            Path("/usr/bin/chromium")
        ]
        candidates["edge"] = [
            Path("/usr/bin/microsoft-edge"),
            Path("/usr/bin/microsoft-edge-stable")
        ]

    return candidates


def detect_installed_browsers() -> Dict[str, Optional[str]]:
    """Scan and return all installed browsers with their resolved executable paths."""
    candidates = get_default_browser_candidates()
    detected: Dict[str, Optional[str]] = {}

    for b_type, path_list in candidates.items():
        found_path = None
        for p in path_list:
            if p and p.is_file() and p.exists():
                found_path = str(p.resolve())
                break
        detected[b_type] = found_path

    return detected


def resolve_browser_executable(preferred_type: str = "auto", custom_path: Optional[str] = None) -> Dict[str, str]:
    """
    Resolve the active browser executable path based on user preference or auto-detection.
    Returns dict: {'browser_type': ..., 'executable_path': ...}
    """
    if custom_path and Path(custom_path).is_file():
        return {
            "browser_type": "custom",
            "executable_path": str(Path(custom_path).resolve())
        }

    detected = detect_installed_browsers()
    preferred = (preferred_type or "auto").lower()

    if preferred != "auto" and detected.get(preferred):
        return {
            "browser_type": preferred,
            "executable_path": detected[preferred]  # type: ignore
        }

    # Auto priority: Brave -> Chrome -> Edge -> Playwright Chromium
    for b in ["brave", "chrome", "edge", "chromium"]:
        if detected.get(b):
            return {
                "browser_type": b,
                "executable_path": detected[b]  # type: ignore
            }

    # If no browser was found at standard paths, let Playwright use its default
    return {
        "browser_type": "playwright-default",
        "executable_path": ""
    }


class AgentSettings(BaseModel):
    """Runtime configuration for browser automation and MCP server."""
    # LLM Settings
    google_api_key: str = Field(default_factory=lambda: os.getenv("GOOGLE_API_KEY", ""))
    gemini_api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    openai_api_key: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    anthropic_api_key: str = Field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    local_ai_base_url: str = Field(default_factory=lambda: os.getenv("LOCAL_AI_BASE_URL", "http://localhost:11434/v1"))
    local_ai_model: str = Field(default_factory=lambda: os.getenv("LOCAL_AI_MODEL", "llama3.2"))
    custom_openai_base_url: str = Field(default_factory=lambda: os.getenv("CUSTOM_OPENAI_BASE_URL", ""))
    custom_openai_model: str = Field(default_factory=lambda: os.getenv("CUSTOM_OPENAI_MODEL", ""))
    custom_openai_api_key: str = Field(default_factory=lambda: os.getenv("CUSTOM_OPENAI_API_KEY", ""))
    default_llm_provider: str = Field(default_factory=lambda: os.getenv("DEFAULT_LLM_PROVIDER", "google"))
    default_llm_model: str = Field(default_factory=lambda: os.getenv("DEFAULT_LLM_MODEL", "gemini-3.5-flash"))

    # Browser Settings
    browser_type: str = Field(default_factory=lambda: os.getenv("BROWSER_TYPE", "auto"))
    browser_executable_path: str = Field(default_factory=lambda: os.getenv("BROWSER_EXECUTABLE_PATH", ""))
    headless: bool = Field(
        default_factory=lambda: os.getenv("BROWSER_HEADLESS", "false").lower() in ("true", "1", "yes")
    )
    window_width: int = Field(default_factory=lambda: int(os.getenv("WINDOW_WIDTH", "1280")))
    window_height: int = Field(default_factory=lambda: int(os.getenv("WINDOW_HEIGHT", "900")))
    cdp_url: Optional[str] = Field(default_factory=lambda: os.getenv("CDP_URL") or None)


# Default settings singleton
settings = AgentSettings()
