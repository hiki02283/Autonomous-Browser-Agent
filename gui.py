"""
FastAPI Modern Graphical User Interface for Autonomous Browser Agent.
Provides a single-page interactive dashboard with real-time SSE streaming,
custom preset manager, API connection tester, Custom OpenAI-compatible endpoints,
live execution logs, and Human-in-the-Loop intervention controls.

Launch via:
    python gui.py
"""

import os
import sys
import json
import asyncio
import webbrowser
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager

# Reconfigure stdout for UTF-8 on Windows
sys.stdout.reconfigure(encoding="utf-8")

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from src.config import settings, detect_installed_browsers, resolve_browser_executable
from src.agent import run_autonomous_task, test_llm_connection, fetch_available_models

# --- App State and Event Bus ---
class TaskState:
    def __init__(self):
        self.is_running: bool = False
        self.current_task: Optional[str] = None
        self.active_agent: Any = None
        self.human_alert: Optional[str] = None
        self.human_timeout: int = 60
        self.resume_event: asyncio.Event = asyncio.Event()
        self.event_subscribers: List[asyncio.Queue] = []
        self.logs: List[Dict[str, Any]] = []
        self.last_result: Optional[Dict[str, Any]] = None

    async def broadcast(self, event_type: str, data: Any):
        """Broadcast an event to all connected SSE clients."""
        payload = {"type": event_type, "data": data}
        self.logs.append(payload)
        for queue in list(self.event_subscribers):
            try:
                await queue.put(payload)
            except Exception:
                pass


state = TaskState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if state.active_agent:
        try:
            state.active_agent.stop()
        except Exception:
            pass


app = FastAPI(title="Autonomous Browser Agent GUI", lifespan=lifespan)


class RunTaskRequest(BaseModel):
    task: str
    browser: str = "auto"
    headless: bool = False
    max_steps: int = 30
    provider: Optional[str] = "google"
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class CheckConnectionRequest(BaseModel):
    provider: str = "google"
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class ListModelsRequest(BaseModel):
    provider: str = "google"
    api_key: Optional[str] = None
    base_url: Optional[str] = None


@app.post("/api/list-models")
async def list_models_endpoint(req: ListModelsRequest):
    """Fetch available models for a given provider, credentials, and endpoint."""
    res = await fetch_available_models(
        provider=req.provider,
        api_key=req.api_key.strip() if req.api_key and req.api_key.strip() else None,
        base_url=req.base_url.strip() if req.base_url and req.base_url.strip() else None,
    )
    return res


@app.get("/api/status")
def get_system_status():
    """Return local browser binaries, configuration settings, and API key statuses."""
    detected = detect_installed_browsers()
    resolved = resolve_browser_executable()
    google_key = bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
    openai_key = bool(os.getenv("OPENAI_API_KEY"))
    anthropic_key = bool(os.getenv("ANTHROPIC_API_KEY"))
    custom_key = bool(os.getenv("CUSTOM_OPENAI_API_KEY"))

    return {
        "detected_browsers": detected,
        "resolved_browser": resolved,
        "is_running": state.is_running,
        "human_alert": state.human_alert,
        "settings": {
            "default_headless": settings.headless,
            "window_size": f"{settings.window_width}x{settings.window_height}",
            "default_provider": settings.default_llm_provider,
            "default_model": settings.default_llm_model,
            "local_ai_base_url": settings.local_ai_base_url,
            "local_ai_model": settings.local_ai_model,
            "custom_openai_base_url": settings.custom_openai_base_url,
            "custom_openai_model": settings.custom_openai_model,
        },
        "api_keys": {
            "google_gemini": google_key,
            "openai": openai_key,
            "anthropic": anthropic_key,
            "custom_openai": custom_key,
        }
    }


@app.post("/api/check-connection")
async def check_connection_endpoint(req: CheckConnectionRequest):
    """Test API credentials and network reachability by sending a lightweight probe."""
    res = await test_llm_connection(
        provider=req.provider,
        model=req.model,
        api_key=req.api_key.strip() if req.api_key and req.api_key.strip() else None,
        base_url=req.base_url.strip() if req.base_url and req.base_url.strip() else None,
    )
    return res


@app.get("/api/events")
async def sse_events(request: Request):
    """Server-Sent Events endpoint streaming real-time agent updates to the GUI."""
    queue = asyncio.Queue()
    state.event_subscribers.append(queue)

    async def event_generator():
        try:
            # Send initial state snapshot
            await queue.put({
                "type": "init",
                "data": {
                    "is_running": state.is_running,
                    "human_alert": state.human_alert,
                    "last_result": state.last_result
                }
            })
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            if queue in state.event_subscribers:
                state.event_subscribers.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.post("/api/run")
async def start_task(req: RunTaskRequest):
    """Launch an autonomous browser task in the background."""
    if state.is_running:
        raise HTTPException(status_code=400, detail="A task is already running.")

    if not req.task.strip():
        raise HTTPException(status_code=400, detail="Task description cannot be empty.")

    state.is_running = True
    state.current_task = req.task
    state.human_alert = None
    state.last_result = None
    state.resume_event.clear()

    provider_label = req.provider or "google"
    model_label = req.model or "default"

    await state.broadcast("status", {"is_running": True, "task": req.task})
    await state.broadcast("log", {
        "level": "info",
        "message": f"Starting task with browser '{req.browser}' | LLM: {provider_label} ({model_label}) | Headless={req.headless} | Max Steps={req.max_steps}"
    })

    # Async background task runner
    async def _worker():
        try:
            async def _step_cb(step_num: int, action: str, thought: Optional[str]):
                await state.broadcast("step", {
                    "step_num": step_num,
                    "action": action,
                    "thought": thought or "",
                    "max_steps": req.max_steps
                })

            async def _human_cb(reason: str, timeout: int):
                state.human_alert = reason
                state.human_timeout = timeout
                await state.broadcast("human_alert", {
                    "active": True,
                    "reason": reason,
                    "timeout": timeout
                })

            def _agent_ref_cb(agent_instance):
                state.active_agent = agent_instance

            result = await run_autonomous_task(
                task=req.task,
                headless=req.headless,
                browser_type=req.browser,
                max_steps=req.max_steps,
                model_name=req.model,
                llm_provider=req.provider,
                api_key=req.api_key.strip() if req.api_key and req.api_key.strip() else None,
                base_url=req.base_url.strip() if req.base_url and req.base_url.strip() else None,
                on_step_callback=_step_cb,
                on_human_intervention=_human_cb,
                resume_event=state.resume_event,
                agent_ref_callback=_agent_ref_cb,
            )

            state.last_result = result
            await state.broadcast("complete", result)
            await state.broadcast("log", {
                "level": "success",
                "message": f"Task completed successfully! Steps: {result.get('total_steps')}"
            })
        except Exception as e:
            err_msg = str(e)
            state.last_result = {"success": False, "error": err_msg}
            await state.broadcast("error", {"error": err_msg})
            await state.broadcast("log", {
                "level": "error",
                "message": f"Error during task: {err_msg}"
            })
        finally:
            state.is_running = False
            state.active_agent = None
            state.human_alert = None
            await state.broadcast("status", {"is_running": False})

    asyncio.create_task(_worker())
    return {"status": "started", "task": req.task}


@app.post("/api/cancel")
async def cancel_task():
    """Cancel the currently running agent task."""
    if not state.is_running:
        return {"status": "not_running"}

    await state.broadcast("log", {"level": "warn", "message": "Cancellation requested by user..."})
    if state.active_agent:
        try:
            state.active_agent.stop()
        except Exception:
            pass

    state.resume_event.set()
    state.is_running = False
    state.human_alert = None
    await state.broadcast("status", {"is_running": False})
    return {"status": "cancelled"}


@app.post("/api/resume")
async def resume_human():
    """Signal that manual human intervention is complete."""
    state.human_alert = None
    state.resume_event.set()
    await state.broadcast("human_alert", {"active": False})
    await state.broadcast("log", {"level": "info", "message": "Human intervention confirmed. Resuming agent..."})
    return {"status": "resumed"}


# --- HTML Dashboard Template ---
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Autonomous Browser Agent by hiki</title>
  <!-- Tailwind CSS CDN -->
  <script src="https://cdn.tailwindcss.com"></script>
  <!-- Lucide Icons -->
  <script src="https://unpkg.com/lucide@latest"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: {
              50: '#f0fdf4',
              500: '#22c55e',
              600: '#16a34a',
              700: '#15803d',
            }
          }
        }
      }
    }
  </script>
  <style>
    @keyframes pulse-slow {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.85; transform: scale(1.02); }
    }
    .animate-pulse-slow {
      animation: pulse-slow 2s infinite ease-in-out;
    }
    .console-scroll::-webkit-scrollbar {
      width: 8px;
    }
    .console-scroll::-webkit-scrollbar-track {
      background: #0f172a;
    }
    .console-scroll::-webkit-scrollbar-thumb {
      background: #334155;
      border-radius: 4px;
    }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col font-sans antialiased selection:bg-brand-500 selection:text-white">

  <!-- Header Navigation -->
  <header class="border-b border-slate-800 bg-slate-900/70 backdrop-blur sticky top-0 z-40">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 py-3.5 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="h-10 w-10 rounded-xl bg-gradient-to-tr from-brand-600 to-emerald-400 flex items-center justify-center shadow-lg shadow-emerald-500/20">
          <i data-lucide="compass" class="w-6 h-6 text-white"></i>
        </div>
        <div>
          <h1 class="font-bold text-lg leading-tight flex items-center gap-2">
            Autonomous Browser Agent
            <span class="text-xs font-normal text-slate-400">by <span class="text-slate-200 font-semibold">hiki</span></span>
            <span class="text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded-full uppercase tracking-wider">v0.1 Local</span>
          </h1>
          <p class="text-xs text-slate-400 flex items-center gap-1.5" id="browser-detected-badge">
            <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
            Detecting local browser...
          </p>
        </div>
      </div>

      <!-- Right System Badges -->
      <div class="flex items-center gap-3">
        <div id="status-pill" class="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 text-slate-300 border border-slate-700">
          <span class="w-2 h-2 rounded-full bg-slate-500" id="status-dot"></span>
          <span id="status-text">Idle</span>
        </div>
        <div id="llm-header-badge-container" class="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs bg-slate-800/80 border border-slate-700 text-slate-300 transition-all">
          <span id="llm-badge-dot" class="w-2 h-2 rounded-full bg-amber-400"></span>
          <span id="llm-key-badge" class="font-medium">LLM: Initializing...</span>
        </div>
      </div>
    </div>
  </header>

  <!-- Main Container -->
  <main class="max-w-7xl mx-auto px-4 sm:px-6 py-6 flex-1 w-full grid grid-cols-1 lg:grid-cols-12 gap-6 relative">

    <!-- Left Column: Controls & Input (7 cols) -->
    <div id="left-column" class="lg:col-span-7 flex flex-col gap-5 transition-all duration-300">

      <!-- Human-in-the-Loop Alert Banner (Hidden by default) -->
      <div id="hitl-banner" class="hidden bg-gradient-to-r from-amber-950/80 to-amber-900/60 border border-amber-500/60 rounded-2xl p-4 shadow-xl shadow-amber-500/10 animate-pulse-slow">
        <div class="flex items-start justify-between gap-4">
          <div class="flex items-start gap-3">
            <div class="p-2 rounded-xl bg-amber-500/20 text-amber-400">
              <i data-lucide="shield-alert" class="w-6 h-6"></i>
            </div>
            <div>
              <h3 class="font-bold text-amber-300 text-sm">Human Intervention Required!</h3>
              <p class="text-xs text-amber-200/90 mt-1" id="hitl-reason">Please solve the CAPTCHA or 2FA challenge in the visible browser window.</p>
              <p class="text-[11px] text-amber-400/80 mt-1 flex items-center gap-1">
                <i data-lucide="eye" class="w-3 h-3"></i> The browser window is currently open on your desktop.
              </p>
            </div>
          </div>
          <button onclick="resumeHumanIntervention()" class="shrink-0 px-4 py-2 rounded-xl bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold text-xs shadow-md transition flex items-center gap-1.5">
            <i data-lucide="check" class="w-4 h-4"></i> I've Completed It
          </button>
        </div>
      </div>

      <!-- Task Input Card -->
      <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-5 shadow-lg backdrop-blur">
        <div class="flex items-center justify-between mb-3">
          <label class="text-sm font-semibold text-slate-200 flex items-center gap-2">
            <i data-lucide="sparkles" class="w-4 h-4 text-brand-500"></i>
            Task Instruction (Natural Language)
          </label>
          <span class="text-xs text-slate-500">Press Ctrl + Enter to run</span>
        </div>
        <textarea id="task-prompt" rows="4" 
          placeholder="e.g. Go to mail.tm, copy the temporary email address, open a second tab to example.com, and check elements..." 
          class="w-full bg-slate-950 border border-slate-700/80 rounded-xl p-3.5 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition resize-none"></textarea>

        <!-- Saved Presets Section (Dynamic localStorage Preset Manager) -->
        <div class="mt-4 pt-3 border-t border-slate-800/80">
          <div class="flex items-center justify-between mb-2.5">
            <div class="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
              <i data-lucide="bookmark" class="w-3.5 h-3.5 text-amber-400"></i> Saved Presets
            </div>
            <button type="button" onclick="openAddPresetModal()" class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 hover:text-white border border-slate-700 hover:border-slate-600 text-xs font-medium transition shadow-sm active:scale-95">
              <i data-lucide="plus" class="w-3.5 h-3.5 text-emerald-400"></i> Add Preset
            </button>
          </div>
          <!-- Dynamic Pill Container -->
          <div id="presets-container" class="flex flex-wrap gap-2 min-h-[32px] items-center">
            <!-- Rendered via JavaScript -->
          </div>
        </div>
      </div>

      <!-- Controls & Settings Card -->
      <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-5 shadow-lg">
        <h3 class="text-sm font-semibold text-slate-200 mb-4 flex items-center gap-2">
          <i data-lucide="sliders" class="w-4 h-4 text-cyan-400"></i>
          Execution Controls & Browser Configuration
        </h3>

        <!-- LLM Configuration Section -->
        <div class="mb-5 p-4 rounded-xl bg-slate-950/60 border border-slate-800">
          <div class="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-1.5">
            <i data-lucide="bot" class="w-3.5 h-3.5 text-emerald-400"></i>
            LLM Provider & Credentials
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <!-- LLM Provider Selector -->
            <div>
              <label class="block text-xs font-medium text-slate-400 mb-1.5">LLM Provider</label>
              <select id="provider-select" onchange="onProviderChange()" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500">
                <option value="google">Google Gemini (Default)</option>
                <option value="openai">OpenAI (GPT-4o / o-series)</option>
                <option value="anthropic">Anthropic (Claude 3.5)</option>
                <option value="local">Local AI (Ollama / LM Studio)</option>
                <option value="custom">Custom OpenAI-Compatible</option>
              </select>
            </div>

            <!-- Dynamic Model Selector & Combo -->
            <div>
              <div class="flex items-center justify-between text-xs font-medium text-slate-400 mb-1.5">
                <span class="flex items-center gap-1.5">
                  Model
                  <span id="model-loading-spinner" class="hidden">
                    <i data-lucide="loader-2" class="w-3 h-3 text-cyan-400 animate-spin"></i>
                  </span>
                </span>
                <button type="button" id="btn-refresh-models" onclick="fetchModelsForCurrentProvider(true)" class="text-[11px] text-cyan-400 hover:text-cyan-300 flex items-center gap-1 transition" title="Fetch latest models from provider">
                  <i data-lucide="refresh-cw" class="w-3 h-3" id="icon-refresh-models"></i>
                  <span>Refresh</span>
                </button>
              </div>

              <!-- Select Menu -->
              <div class="relative">
                <select id="model-select" onchange="onModelSelectChange()" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500 font-mono">
                  <option value="gemini-3.5-flash">gemini-3.5-flash (Recommended)</option>
                  <option value="gemini-3.8-flash">gemini-3.8-flash</option>
                  <option value="gemini-3.5-flash-lite">gemini-3.5-flash-lite</option>
                  <option value="gemini-flash-latest">gemini-flash-latest</option>
                  <option value="gemini-2.5-pro">gemini-2.5-pro</option>
                  <option value="__custom__">-- Other / Custom Model --</option>
                </select>
              </div>

              <!-- Manual text input if "Other / Custom" is selected -->
              <div id="custom-model-container" class="mt-2 hidden">
                <input type="text" id="model-custom-input" placeholder="Type custom model identifier..."
                  class="w-full bg-slate-950 border border-cyan-500/50 rounded-xl px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-cyan-500 font-mono"
                  oninput="onCustomModelInput()">
              </div>
            </div>

            <!-- Base URL Container (For Local AI and Custom OpenAI-Compatible) -->
            <div id="base-url-container" class="sm:col-span-2 hidden">
              <label id="base-url-label" class="block text-xs font-medium text-slate-400 mb-1.5">Base URL</label>
              <input type="text" id="base-url-input" value="http://localhost:11434/v1" placeholder="e.g. https://api.deepseek.com/v1, https://openrouter.ai/api/v1"
                class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500 font-mono"
                oninput="updateHeaderBadge('amber')">
              <p id="base-url-hint" class="text-[11px] text-slate-500 mt-1.5 flex items-center gap-1">
                <i data-lucide="info" class="w-3 h-3 text-cyan-400"></i>
                Compatible with Ollama (11434), LM Studio (1234), or OpenAI-compatible gateways.
              </p>
            </div>

            <!-- API Key Container (For Cloud & Custom Providers) -->
            <div id="api-key-container" class="sm:col-span-2">
              <div class="flex items-center justify-between text-xs font-medium text-slate-400 mb-1.5">
                <span id="api-key-label">API Key (Overrides .env)</span>
              </div>
              <div class="relative">
                <input type="password" id="api-key-input" placeholder="Paste API Key here or leave empty to use .env..."
                  class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 pr-10 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500 font-mono"
                  oninput="updateHeaderBadge('amber')">
                <button type="button" onclick="toggleApiKeyVisibility()" class="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-200 p-1">
                  <i id="eye-icon" data-lucide="eye" class="w-3.5 h-3.5"></i>
                </button>
              </div>
            </div>

            <!-- Test Connection Button & Status Badge -->
            <div class="sm:col-span-2 pt-2 border-t border-slate-800/80 flex flex-wrap items-center justify-between gap-3">
              <div class="flex items-center gap-2">
                <button type="button" id="btn-check-connection" onclick="checkApiConnection()" class="px-3.5 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-700 hover:border-slate-600 text-xs font-semibold text-slate-200 transition flex items-center gap-1.5 shadow-sm active:scale-95">
                  <i data-lucide="plug-zap" class="w-3.5 h-3.5 text-cyan-400"></i>
                  Test Connection
                </button>
                <div id="connection-test-badge" class="hidden text-xs"></div>
              </div>
              <span id="api-key-status" class="text-[11px] text-slate-500">Checking credentials...</span>
            </div>
          </div>
        </div>

        <!-- Browser & Runtime Settings -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <!-- Browser Selection -->
          <div>
            <label class="block text-xs font-medium text-slate-400 mb-1.5">Target Browser</label>
            <select id="browser-select" class="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500">
              <option value="auto">Auto-Detect (Prioritizes Brave)</option>
              <option value="brave">Brave Browser (Local)</option>
              <option value="chromium">Playwright Chromium</option>
              <option value="chrome">Google Chrome</option>
              <option value="edge">Microsoft Edge</option>
            </select>
          </div>

          <!-- Headed / Headless Toggle -->
          <div>
            <label class="block text-xs font-medium text-slate-400 mb-1.5">Execution Mode</label>
            <button type="button" id="toggle-headed-btn" onclick="toggleHeadedMode()" 
              class="w-full flex items-center justify-between px-3.5 py-2 rounded-xl bg-slate-950 border border-emerald-500/40 text-xs text-slate-200 transition">
              <span class="flex items-center gap-2 font-medium">
                <i data-lucide="eye" class="w-4 h-4 text-emerald-400"></i>
                <span id="headed-mode-label">Headed (Visible Window)</span>
              </span>
              <span class="w-2.5 h-2.5 rounded-full bg-emerald-400" id="headed-mode-indicator"></span>
            </button>
          </div>

          <!-- Max Steps Slider with Info Tooltip -->
          <div class="sm:col-span-2">
            <div class="flex items-center justify-between text-xs text-slate-400 mb-1.5">
              <span class="flex items-center gap-1.5">
                Max Autonomous Steps
                <button type="button" onclick="toggleStepsModal(true)" class="inline-flex items-center text-slate-400 hover:text-cyan-400 transition" title="What is Max Steps?">
                  <i data-lucide="info" class="w-3.5 h-3.5"></i>
                </button>
              </span>
              <span id="max-steps-val" class="font-bold text-brand-400 text-sm">30</span>
            </div>
            <input type="range" id="max-steps-slider" min="5" max="60" value="30" step="5"
              oninput="document.getElementById('max-steps-val').textContent = this.value"
              class="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-brand-500">
          </div>
        </div>

        <!-- Action Buttons -->
        <div class="mt-5 pt-4 border-t border-slate-800 flex items-center gap-3">
          <button id="btn-start" onclick="startAgentTask()" class="flex-1 py-3 px-4 rounded-xl bg-gradient-to-r from-emerald-600 to-brand-600 hover:from-emerald-500 hover:to-brand-500 text-white font-bold text-sm shadow-lg shadow-emerald-600/20 transition flex items-center justify-center gap-2 active:scale-[0.98]">
            <i data-lucide="play" class="w-4 h-4 fill-white"></i> Start Autonomous Task
          </button>
          <button id="btn-cancel" onclick="cancelAgentTask()" disabled class="py-3 px-5 rounded-xl bg-slate-800 hover:bg-rose-950 text-slate-500 hover:text-rose-400 border border-slate-700 hover:border-rose-800 font-bold text-sm transition flex items-center justify-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed">
            <i data-lucide="square" class="w-4 h-4"></i> Stop
          </button>
        </div>
      </div>

    </div>

    <!-- Right Column: Live Status & Logs (5 cols) -->
    <div id="right-column" class="lg:col-span-5 flex flex-col gap-5 transition-all duration-300">

      <!-- Live Execution Log Card -->
      <div id="console-card" class="bg-slate-900/80 border border-slate-800 rounded-2xl p-5 shadow-lg flex flex-col h-[620px] transition-all duration-300">
        <div class="flex items-center justify-between pb-3 border-b border-slate-800">
          <div class="flex items-center gap-2">
            <i data-lucide="terminal" class="w-4 h-4 text-brand-400"></i>
            <h3 id="console-title" class="text-sm font-semibold text-slate-200">Live Agent Console</h3>
          </div>
          <div class="flex items-center gap-2">
            <!-- Auto-scroll indicator button -->
            <button id="btn-autoscroll" onclick="toggleAutoScroll()" class="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 flex items-center gap-1 transition" title="Toggle autoscroll">
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400" id="autoscroll-dot"></span>
              <span id="autoscroll-text">Auto-scroll: ON</span>
            </button>
            <span id="step-counter" class="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Step: 0/30</span>
            <!-- Maximize / Full-Context toggle button -->
            <button id="btn-maximize-console" onclick="toggleMaximizeConsole()" title="Maximize / Full-Context mode" class="p-1 rounded text-slate-400 hover:text-cyan-400 hover:bg-slate-800 transition">
              <i id="icon-maximize" data-lucide="maximize-2" class="w-3.5 h-3.5"></i>
            </button>
            <button onclick="clearLogs()" title="Clear console" class="p-1 rounded text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition">
              <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
            </button>
          </div>
        </div>

        <!-- Log Area -->
        <div id="console-logs" class="flex-1 overflow-y-auto console-scroll py-3 font-mono text-xs space-y-2 select-text">
          <div class="text-slate-500">[System] Autonomous Browser Agent GUI ready. Load a preset or type a task prompt.</div>
        </div>

        <!-- Result Card (shown upon completion) -->
        <div id="result-box" class="hidden mt-3 p-3.5 rounded-xl bg-emerald-950/40 border border-emerald-500/30 text-xs">
          <div class="flex items-center justify-between font-bold text-emerald-400 mb-1">
            <span class="flex items-center gap-1.5"><i data-lucide="check-circle" class="w-4 h-4"></i> Final Result</span>
            <span id="result-steps-badge" class="text-[10px] text-slate-400 font-mono"></span>
          </div>
          <div id="result-text" class="text-slate-200 whitespace-pre-wrap max-h-36 overflow-y-auto"></div>
        </div>
      </div>

    </div>

  </main>

  <!-- Add Preset Modal -->
  <div id="add-preset-modal" class="hidden fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm" onclick="if(event.target === this) closeAddPresetModal()">
    <div class="bg-slate-900 border border-slate-700/80 rounded-2xl max-w-md w-full p-6 shadow-2xl animate-in fade-in zoom-in duration-150">
      <div class="flex items-center justify-between pb-3 border-b border-slate-800">
        <div class="flex items-center gap-2 text-emerald-400 font-bold text-sm">
          <i data-lucide="bookmark-plus" class="w-4 h-4"></i>
          Create Custom Preset
        </div>
        <button onclick="closeAddPresetModal()" class="text-slate-400 hover:text-slate-200 p-1 rounded-lg hover:bg-slate-800 transition">
          <i data-lucide="x" class="w-4 h-4"></i>
        </button>
      </div>
      <div class="mt-4 space-y-3.5">
        <div>
          <label class="block text-xs font-medium text-slate-400 mb-1">Preset Title</label>
          <input type="text" id="preset-title-input" placeholder="e.g. Account Signup Flow" class="w-full bg-slate-950 border border-slate-700 rounded-xl px-3 py-2 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-400 mb-1">Preset Prompt / Instructions</label>
          <textarea id="preset-prompt-input" rows="4" placeholder="Enter the exact task instruction you want this preset to load into the task prompt..." class="w-full bg-slate-950 border border-slate-700 rounded-xl p-3 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500 resize-none"></textarea>
        </div>
      </div>
      <div class="mt-5 flex items-center justify-end gap-2.5">
        <button onclick="closeAddPresetModal()" class="px-3.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-slate-300 transition">
          Cancel
        </button>
        <button onclick="saveCustomPreset()" class="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-xs font-bold text-white transition flex items-center gap-1.5 shadow-md shadow-emerald-600/20 active:scale-95">
          <i data-lucide="check" class="w-3.5 h-3.5"></i> Save Preset
        </button>
      </div>
    </div>
  </div>

  <!-- Info Popover Modal for "Max Autonomous Steps" -->
  <div id="steps-modal" class="hidden fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm" onclick="if(event.target === this) toggleStepsModal(false)">
    <div class="bg-slate-900 border border-slate-700/80 rounded-2xl max-w-md w-full p-6 shadow-2xl animate-in fade-in zoom-in duration-150">
      <div class="flex items-center justify-between pb-3 border-b border-slate-800">
        <div class="flex items-center gap-2 text-cyan-400 font-bold text-sm">
          <i data-lucide="info" class="w-4 h-4"></i>
          Max Autonomous Steps
        </div>
        <button onclick="toggleStepsModal(false)" class="text-slate-400 hover:text-slate-200 p-1 rounded-lg hover:bg-slate-800 transition">
          <i data-lucide="x" class="w-4 h-4"></i>
        </button>
      </div>
      <p class="text-xs text-slate-300 mt-3 leading-relaxed">
        <strong>Max Autonomous Steps</strong> defines the upper limit of actions (clicks, inputs, tab switches, page loads) the agent can perform for a single task. This acts as a safety guardrail to prevent infinite loops, quota exhaustion, or agent stalls on complex pages.
      </p>
      <div class="mt-4 flex justify-end">
        <button onclick="toggleStepsModal(false)" class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-slate-200 transition">
          Understood
        </button>
      </div>
    </div>
  </div>

  <footer class="border-t border-slate-800/80 py-3 text-center text-xs text-slate-500">
    Unified Local Autonomous Browser Agent by hiki &bull; Powered by browser-use & Playwright
  </footer>

  <!-- JavaScript Client Logic -->
  <script>
    let isHeaded = true;
    let isMaximized = false;
    let autoScroll = true;
    let eventSource = null;
    let systemStatusCache = null;

    const PRESETS_STORAGE_KEY = 'browser_agent_custom_presets_v1';

    // Model Defaults per Provider
    const PROVIDER_DEFAULTS = {
      google: "gemini-3.5-flash",
      openai: "gpt-4o-mini",
      anthropic: "claude-3-5-sonnet-latest",
      local: "llama3.2",
      custom: "deepseek-chat"
    };

    const BASE_URL_DEFAULTS = {
      local: "http://localhost:11434/v1",
      custom: "https://api.deepseek.com/v1"
    };

    // --- Dynamic Model Selector Combo & Header Status ---
    let currentBadgeHealth = 'amber'; // 'green', 'amber', 'red'

    function getProviderDisplayName(provider) {
      switch (provider) {
        case 'google': return 'Gemini';
        case 'openai': return 'OpenAI';
        case 'anthropic': return 'Claude';
        case 'local': return 'Local AI';
        case 'custom': return 'Custom';
        default: return provider ? (provider.charAt(0).toUpperCase() + provider.slice(1)) : 'LLM';
      }
    }

    function updateHeaderBadge(health = null) {
      if (health) {
        currentBadgeHealth = health;
      }

      const providerSelect = document.getElementById('provider-select');
      const provider = providerSelect ? providerSelect.value : 'google';
      const model = getSelectedModel();
      const badge = document.getElementById('llm-key-badge');
      const dot = document.getElementById('llm-badge-dot');
      const container = document.getElementById('llm-header-badge-container');

      if (!badge) return;

      const providerName = getProviderDisplayName(provider);
      const displayModel = model || 'Loading...';

      let textClass = "text-amber-400";
      let dotClass = "w-2 h-2 rounded-full bg-amber-400 shadow-sm shadow-amber-400/50";
      let containerBorder = "border-amber-500/40";
      let statusHint = "Unverified (Click 'Test Connection')";

      if (currentBadgeHealth === 'green') {
        textClass = "text-emerald-400";
        dotClass = "w-2 h-2 rounded-full bg-emerald-400 shadow-sm shadow-emerald-400/50";
        containerBorder = "border-emerald-500/40";
        statusHint = "Verified & Active";
      } else if (currentBadgeHealth === 'red') {
        textClass = "text-rose-400";
        dotClass = "w-2 h-2 rounded-full bg-rose-400 shadow-sm shadow-rose-400/50";
        containerBorder = "border-rose-500/40";
        statusHint = "Connection Failed / Offline";
      }

      badge.innerHTML = `<span class="${textClass} font-semibold">${providerName}: ${displayModel}</span>`;
      if (dot) dot.className = dotClass;
      if (container) {
        container.className = `hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs bg-slate-800/80 border ${containerBorder} text-slate-300 transition-all`;
        container.title = `${providerName}: ${displayModel} - ${statusHint}`;
      }
    }

    function getSelectedModel() {
      const sel = document.getElementById('model-select');
      if (!sel) return "gemini-3.5-flash";
      if (sel.value === '__custom__') {
        const customInput = document.getElementById('model-custom-input');
        return (customInput && customInput.value.trim()) ? customInput.value.trim() : (PROVIDER_DEFAULTS[document.getElementById('provider-select').value] || "gemini-3.5-flash");
      }
      return sel.value || PROVIDER_DEFAULTS[document.getElementById('provider-select').value] || "gemini-3.5-flash";
    }

    function onModelSelectChange() {
      const sel = document.getElementById('model-select');
      const customContainer = document.getElementById('custom-model-container');
      const customInput = document.getElementById('model-custom-input');
      if (sel.value === '__custom__') {
        customContainer.classList.remove('hidden');
        if (customInput) customInput.focus();
      } else {
        customContainer.classList.add('hidden');
      }
      updateHeaderBadge('amber');
    }

    function onCustomModelInput() {
      updateHeaderBadge('amber');
    }

    let isFetchingModels = false;

    async function fetchModelsForCurrentProvider(manualClick = false) {
      if (isFetchingModels) return;
      isFetchingModels = true;

      const provider = document.getElementById('provider-select').value;
      const apiKey = document.getElementById('api-key-input').value.trim();
      const baseUrl = document.getElementById('base-url-input').value.trim();
      const spinner = document.getElementById('model-loading-spinner');
      const iconRefresh = document.getElementById('icon-refresh-models');
      const select = document.getElementById('model-select');

      if (spinner) spinner.classList.remove('hidden');
      if (iconRefresh) iconRefresh.classList.add('animate-spin');

      try {
        const payload = { provider };
        if (apiKey) payload.api_key = apiKey;
        if (baseUrl) payload.base_url = baseUrl;

        const res = await fetch('/api/list-models', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.models && data.models.length > 0) {
          const previousValue = select.value;
          select.innerHTML = '';

          data.models.forEach((modelName) => {
            const opt = document.createElement('option');
            opt.value = modelName;
            if (modelName === data.recommended) {
              opt.textContent = `${modelName} (Recommended)`;
            } else {
              opt.textContent = modelName;
            }
            select.appendChild(opt);
          });

          // Always append "Other / Custom"
          const customOpt = document.createElement('option');
          customOpt.value = '__custom__';
          customOpt.textContent = '-- Other / Custom Model --';
          select.appendChild(customOpt);

          // Select logic
          if (previousValue && previousValue !== '__custom__' && data.models.includes(previousValue)) {
            select.value = previousValue;
          } else if (data.recommended && data.models.includes(data.recommended)) {
            select.value = data.recommended;
          } else {
            select.value = data.models[0];
          }

          onModelSelectChange();
          if (manualClick) {
            appendLog('info', `Fetched ${data.models.length} models for ${provider.toUpperCase()}`);
          }
        } else if (!data.ok && manualClick) {
          appendLog('warn', `Model listing returned: ${data.error || 'No models found'}`);
        }
      } catch (err) {
        if (manualClick) {
          appendLog('error', `Failed to fetch models: ${err.message}`);
        }
      } finally {
        if (spinner) spinner.classList.add('hidden');
        if (iconRefresh) iconRefresh.classList.remove('animate-spin');
        isFetchingModels = false;
        lucide.createIcons();
      }
    }

    // --- Dynamic Custom Presets ---
    function getStoredPresets() {
      try {
        const raw = localStorage.getItem(PRESETS_STORAGE_KEY);
        return raw ? JSON.parse(raw) : [];
      } catch (e) {
        return [];
      }
    }

    function saveStoredPresets(presets) {
      try {
        localStorage.setItem(PRESETS_STORAGE_KEY, JSON.stringify(presets));
      } catch (e) {}
    }

    function renderPresets() {
      const container = document.getElementById('presets-container');
      const presets = getStoredPresets();

      if (presets.length === 0) {
        container.innerHTML = `<span class="text-xs text-slate-500 italic flex items-center gap-1">No presets saved. Click '+ Add Preset' to create your first one.</span>`;
        return;
      }

      container.innerHTML = '';
      presets.forEach((p, idx) => {
        const pill = document.createElement('div');
        pill.className = "group inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-slate-800/80 hover:bg-slate-800 border border-slate-700/80 hover:border-slate-600 text-xs text-slate-200 cursor-pointer transition shadow-sm";
        pill.title = `Click to load preset into prompt:\\n"${p.title}"`;

        pill.onclick = (e) => {
          if (e.target.closest('.delete-preset-btn')) return;
          document.getElementById('task-prompt').value = p.prompt;
          document.getElementById('task-prompt').focus();
        };

        const titleSpan = document.createElement('span');
        titleSpan.className = "font-medium truncate max-w-[200px]";
        titleSpan.textContent = p.title;

        const delBtn = document.createElement('button');
        delBtn.type = "button";
        delBtn.className = "delete-preset-btn p-0.5 rounded text-slate-500 hover:text-rose-400 hover:bg-slate-700 transition ml-0.5";
        delBtn.title = "Delete preset";
        delBtn.innerHTML = `
          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="w-3 h-3"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
        `;
        delBtn.onclick = (e) => {
          e.stopPropagation();
          deletePreset(idx);
        };

        pill.appendChild(titleSpan);
        pill.appendChild(delBtn);
        container.appendChild(pill);
      });
    }

    function openAddPresetModal() {
      document.getElementById('preset-title-input').value = '';
      document.getElementById('preset-prompt-input').value = '';
      document.getElementById('add-preset-modal').classList.remove('hidden');
      document.getElementById('preset-title-input').focus();
    }

    function closeAddPresetModal() {
      document.getElementById('add-preset-modal').classList.add('hidden');
    }

    function saveCustomPreset() {
      const title = document.getElementById('preset-title-input').value.trim();
      const prompt = document.getElementById('preset-prompt-input').value.trim();

      if (!title) {
        alert("Please enter a preset title.");
        return;
      }
      if (!prompt) {
        alert("Please enter the preset prompt instructions.");
        return;
      }

      const presets = getStoredPresets();
      presets.push({ title, prompt, id: Date.now() });
      saveStoredPresets(presets);
      closeAddPresetModal();
      renderPresets();
    }

    function deletePreset(index) {
      const presets = getStoredPresets();
      if (index >= 0 && index < presets.length) {
        presets.splice(index, 1);
        saveStoredPresets(presets);
        renderPresets();
      }
    }

    // --- Provider and Model Changes ---
    function onProviderChange() {
      const provider = document.getElementById('provider-select').value;
      const apiKeyContainer = document.getElementById('api-key-container');
      const baseUrlContainer = document.getElementById('base-url-container');
      const baseUrlInput = document.getElementById('base-url-input');
      const baseUrlLabel = document.getElementById('base-url-label');
      const baseUrlHint = document.getElementById('base-url-hint');
      const apiKeyLabel = document.getElementById('api-key-label');
      const apiKeyStatus = document.getElementById('api-key-status');
      const testBadge = document.getElementById('connection-test-badge');

      // Clear connection badge on provider switch
      if (testBadge) testBadge.classList.add('hidden');

      if (provider === 'custom') {
        // Expose BOTH Base URL and API Key
        baseUrlContainer.classList.remove('hidden');
        apiKeyContainer.classList.remove('hidden');
        baseUrlLabel.textContent = "Custom Base URL";
        baseUrlInput.placeholder = "e.g. https://openrouter.ai/api/v1, https://api.deepseek.com/v1";
        if (!baseUrlInput.value || baseUrlInput.value === BASE_URL_DEFAULTS.local) {
          baseUrlInput.value = BASE_URL_DEFAULTS.custom;
        }
        baseUrlHint.innerHTML = `<i data-lucide="info" class="w-3 h-3 text-cyan-400"></i> Works with OpenRouter, DeepSeek, Groq, Together, or any standard OpenAI-compatible API.`;
        apiKeyLabel.textContent = "API Key";
        apiKeyStatus.innerHTML = '<span class="text-cyan-400 font-medium">Custom endpoint active</span>';

      } else if (provider === 'local') {
        // Expose Base URL only, hide API Key
        baseUrlContainer.classList.remove('hidden');
        apiKeyContainer.classList.add('hidden');
        baseUrlLabel.textContent = "Local AI Base URL";
        baseUrlInput.placeholder = "http://localhost:11434/v1";
        baseUrlInput.value = BASE_URL_DEFAULTS.local;
        baseUrlHint.innerHTML = `<i data-lucide="info" class="w-3 h-3 text-emerald-400"></i> Compatible with Ollama (port 11434) and LM Studio (port 1234). No API key required.`;
        apiKeyStatus.innerHTML = '<span class="text-emerald-400 font-medium">Local endpoint active</span>';

      } else {
        // Cloud providers: Google, OpenAI, Anthropic
        baseUrlContainer.classList.add('hidden');
        apiKeyContainer.classList.remove('hidden');
        apiKeyLabel.textContent = "API Key (Overrides .env)";

        if (systemStatusCache && systemStatusCache.api_keys) {
          let hasKey = false;
          if (provider === 'google' && systemStatusCache.api_keys.google_gemini) hasKey = true;
          if (provider === 'openai' && systemStatusCache.api_keys.openai) hasKey = true;
          if (provider === 'anthropic' && systemStatusCache.api_keys.anthropic) hasKey = true;

          if (hasKey) {
            apiKeyStatus.innerHTML = '<span class="text-emerald-400 font-medium">Active in .env (leave blank to use)</span>';
          } else {
            apiKeyStatus.innerHTML = '<span class="text-amber-400 font-medium">Not found in .env (enter key below)</span>';
          }
        }
      }
      lucide.createIcons();
      // Update dynamic header badge on provider change
      updateHeaderBadge('amber');
      // Auto-fetch available models for this provider
      fetchModelsForCurrentProvider(false);
    }

    // --- API Connection Test ---
    async function checkApiConnection() {
      const provider = document.getElementById('provider-select').value;
      const model = getSelectedModel();
      const apiKey = document.getElementById('api-key-input').value.trim();
      const baseUrl = document.getElementById('base-url-input').value.trim();
      const badge = document.getElementById('connection-test-badge');
      const btn = document.getElementById('btn-check-connection');

      btn.disabled = true;
      badge.className = "text-xs text-cyan-400 animate-pulse flex items-center gap-1 font-mono";
      badge.innerHTML = '<svg class="animate-spin w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"/><path d="M12 2a10 10 0 0 1 10 10"/></svg> Testing...';
      badge.classList.remove('hidden');

      try {
        const payload = {
          provider: provider,
          model: model,
          api_key: apiKey || null,
          base_url: (provider === 'local' || provider === 'custom') ? (baseUrl || null) : null
        };

        const res = await fetch('/api/check-connection', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.ok) {
          badge.className = "text-xs text-emerald-400 font-medium flex items-center gap-1.5 bg-emerald-500/10 border border-emerald-500/30 px-2.5 py-1 rounded-lg";
          badge.innerHTML = `<i data-lucide="check-circle" class="w-3.5 h-3.5 text-emerald-400"></i> ${data.message}`;
          // Set header badge to Green / Active
          updateHeaderBadge('green');
          // Refresh model list on successful connection
          fetchModelsForCurrentProvider(false);
        } else {
          badge.className = "text-xs text-rose-400 font-medium flex items-center gap-1.5 bg-rose-500/10 border border-rose-500/30 px-2.5 py-1 rounded-lg";
          badge.innerHTML = `<i data-lucide="alert-circle" class="w-3.5 h-3.5 text-rose-400 shrink-0"></i> <span class="truncate max-w-[280px]" title="${data.error}">${data.error}</span>`;
          // Set header badge to Red / Offline
          updateHeaderBadge('red');
        }
      } catch (err) {
        badge.className = "text-xs text-rose-400 font-medium flex items-center gap-1.5 bg-rose-500/10 border border-rose-500/30 px-2.5 py-1 rounded-lg";
        badge.innerHTML = `<i data-lucide="alert-circle" class="w-3.5 h-3.5 text-rose-400 shrink-0"></i> <span>Network Error: ${err.message}</span>`;
        // Set header badge to Red / Offline
        updateHeaderBadge('red');
      } finally {
        btn.disabled = false;
        lucide.createIcons();
      }
    }

    function toggleApiKeyVisibility() {
      const input = document.getElementById('api-key-input');
      const icon = document.getElementById('eye-icon');
      if (input.type === 'password') {
        input.type = 'text';
        icon.setAttribute('data-lucide', 'eye-off');
      } else {
        input.type = 'password';
        icon.setAttribute('data-lucide', 'eye');
      }
      lucide.createIcons();
    }

    function toggleStepsModal(show) {
      const modal = document.getElementById('steps-modal');
      if (show) {
        modal.classList.remove('hidden');
      } else {
        modal.classList.add('hidden');
      }
    }

    function toggleMaximizeConsole() {
      isMaximized = !isMaximized;
      const card = document.getElementById('console-card');
      const icon = document.getElementById('icon-maximize');
      const title = document.getElementById('console-title');

      if (isMaximized) {
        card.className = "fixed inset-0 z-50 p-6 bg-slate-950/95 backdrop-blur-md flex flex-col h-screen w-screen border-none rounded-none shadow-2xl animate-in fade-in duration-200";
        icon.setAttribute('data-lucide', 'minimize-2');
        title.innerHTML = 'Live Agent Console <span class="text-xs text-cyan-400 font-normal ml-2">(Full-Context Inspection Mode)</span>';
      } else {
        card.className = "bg-slate-900/80 border border-slate-800 rounded-2xl p-5 shadow-lg flex flex-col h-[620px] transition-all duration-300";
        icon.setAttribute('data-lucide', 'maximize-2');
        title.textContent = 'Live Agent Console';
      }
      lucide.createIcons();
    }

    function toggleAutoScroll() {
      autoScroll = !autoScroll;
      const text = document.getElementById('autoscroll-text');
      const dot = document.getElementById('autoscroll-dot');
      const btn = document.getElementById('btn-autoscroll');

      if (autoScroll) {
        text.textContent = "Auto-scroll: ON";
        dot.className = "w-1.5 h-1.5 rounded-full bg-emerald-400";
        btn.className = "text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 flex items-center gap-1 transition";
      } else {
        text.textContent = "Auto-scroll: OFF";
        dot.className = "w-1.5 h-1.5 rounded-full bg-slate-500";
        btn.className = "text-[10px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700 flex items-center gap-1 transition";
      }
    }

    function toggleHeadedMode() {
      isHeaded = !isHeaded;
      const label = document.getElementById('headed-mode-label');
      const ind = document.getElementById('headed-mode-indicator');
      const btn = document.getElementById('toggle-headed-btn');

      if (isHeaded) {
        label.textContent = "Headed (Visible Window)";
        ind.className = "w-2.5 h-2.5 rounded-full bg-emerald-400";
        btn.className = "w-full flex items-center justify-between px-3.5 py-2 rounded-xl bg-slate-950 border border-emerald-500/40 text-xs text-slate-200 transition";
      } else {
        label.textContent = "Headless (Background)";
        ind.className = "w-2.5 h-2.5 rounded-full bg-amber-400";
        btn.className = "w-full flex items-center justify-between px-3.5 py-2 rounded-xl bg-slate-950 border border-amber-500/40 text-xs text-slate-200 transition";
      }
    }

    function appendLog(level, message, timestamp) {
      const logs = document.getElementById('console-logs');
      const entry = document.createElement('div');
      const timeStr = timestamp || new Date().toLocaleTimeString();

      let colorClass = "text-slate-300";
      let tagColor = "text-slate-500";
      let tagText = "INFO";

      if (level === 'error') {
        colorClass = "text-rose-400";
        tagColor = "text-rose-500";
        tagText = "ERROR";
      } else if (level === 'success') {
        colorClass = "text-emerald-400";
        tagColor = "text-emerald-500";
        tagText = "SUCCESS";
      } else if (level === 'step') {
        colorClass = "text-cyan-300";
        tagColor = "text-cyan-500";
        tagText = "STEP";
      } else if (level === 'warn') {
        colorClass = "text-amber-400";
        tagColor = "text-amber-500";
        tagText = "WARN";
      }

      entry.innerHTML = `<span class="${tagColor}">[${timeStr}] [${tagText}]</span> <span class="${colorClass}">${message}</span>`;
      logs.appendChild(entry);
      if (autoScroll) {
        logs.scrollTop = logs.scrollHeight;
      }
    }

    function clearLogs() {
      document.getElementById('console-logs').innerHTML = '';
    }

    async function fetchSystemStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        systemStatusCache = data;
        
        // Update browser badge
        const badge = document.getElementById('browser-detected-badge');
        if (data.resolved_browser && data.resolved_browser.executable_path) {
          badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400"></span> ${data.resolved_browser.browser_type.toUpperCase()} (${data.resolved_browser.executable_path})`;
        } else {
          badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400"></span> Playwright Chromium`;
        }

        // Initialize dynamic header badge
        updateHeaderBadge('amber');

        onProviderChange();
      } catch (err) {
        console.error("Failed to fetch system status:", err);
      }
    }

    function setupSSE() {
      if (eventSource) {
        eventSource.close();
      }

      eventSource = new EventSource('/api/events');

      eventSource.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          handleServerEvent(payload);
        } catch (e) {}
      };

      eventSource.onerror = () => {
        setTimeout(setupSSE, 3000);
      };
    }

    function handleServerEvent(event) {
      const type = event.type;
      const data = event.data;

      if (type === 'init') {
        updateRunningUI(data.is_running);
        if (data.human_alert) {
          showHumanAlert(data.human_alert);
        }
        if (data.last_result) {
          showResult(data.last_result);
        }
      } else if (type === 'status') {
        updateRunningUI(data.is_running);
      } else if (type === 'log') {
        appendLog(data.level, data.message);
      } else if (type === 'step') {
        document.getElementById('step-counter').textContent = `Step: ${data.step_num}/${data.max_steps}`;
        let details = `Step ${data.step_num}: ${data.action}`;
        if (data.thought) {
          details += `\\nThought: ${data.thought}`;
        }
        appendLog('step', details);
      } else if (type === 'human_alert') {
        if (data.active) {
          showHumanAlert(data.reason);
        } else {
          hideHumanAlert();
        }
      } else if (type === 'complete') {
        showResult(data);
        updateRunningUI(false);
      } else if (type === 'error') {
        appendLog('error', data.error);
        updateRunningUI(false);
      }
    }

    function updateRunningUI(isRunning) {
      const btnStart = document.getElementById('btn-start');
      const btnCancel = document.getElementById('btn-cancel');
      const dot = document.getElementById('status-dot');
      const text = document.getElementById('status-text');

      if (isRunning) {
        btnStart.disabled = true;
        btnStart.classList.add('opacity-50', 'cursor-not-allowed');
        btnCancel.disabled = false;
        btnCancel.classList.remove('text-slate-500', 'border-slate-700');
        btnCancel.classList.add('text-rose-400', 'border-rose-800', 'bg-rose-950/40');
        dot.className = "w-2 h-2 rounded-full bg-emerald-400 animate-ping";
        text.textContent = "Agent Running";
      } else {
        btnStart.disabled = false;
        btnStart.classList.remove('opacity-50', 'cursor-not-allowed');
        btnCancel.disabled = true;
        btnCancel.classList.add('text-slate-500', 'border-slate-700');
        btnCancel.classList.remove('text-rose-400', 'border-rose-800', 'bg-rose-950/40');
        dot.className = "w-2 h-2 rounded-full bg-slate-500";
        text.textContent = "Idle";
      }
    }

    function showHumanAlert(reason) {
      const banner = document.getElementById('hitl-banner');
      document.getElementById('hitl-reason').textContent = reason || "Please complete manual verification in the browser window.";
      banner.classList.remove('hidden');
      appendLog('warn', `Human intervention requested: ${reason}`);
    }

    function hideHumanAlert() {
      document.getElementById('hitl-banner').classList.add('hidden');
    }

    async function resumeHumanIntervention() {
      hideHumanAlert();
      await fetch('/api/resume', { method: 'POST' });
    }

    function showResult(data) {
      const box = document.getElementById('result-box');
      const text = document.getElementById('result-text');
      const badge = document.getElementById('result-steps-badge');

      box.classList.remove('hidden');
      text.textContent = data.final_result || JSON.stringify(data, null, 2);
      badge.textContent = `Steps taken: ${data.total_steps || 'N/A'}`;
    }

    async function startAgentTask() {
      const prompt = document.getElementById('task-prompt').value.trim();
      if (!prompt) {
        alert("Please enter a task prompt first.");
        return;
      }

      document.getElementById('result-box').classList.add('hidden');
      hideHumanAlert();

      const browser = document.getElementById('browser-select').value;
      const maxSteps = parseInt(document.getElementById('max-steps-slider').value, 10);
      const provider = document.getElementById('provider-select').value;
      const model = getSelectedModel();
      const apiKey = document.getElementById('api-key-input').value.trim();
      const baseUrl = document.getElementById('base-url-input').value.trim();

      const payload = {
        task: prompt,
        browser: browser,
        headless: !isHeaded,
        max_steps: maxSteps,
        provider: provider,
        model: model,
      };

      if (provider === 'local') {
        payload.base_url = baseUrl || "http://localhost:11434/v1";
        payload.api_key = "ollama";
      } else if (provider === 'custom') {
        payload.base_url = baseUrl || "https://api.deepseek.com/v1";
        if (apiKey) payload.api_key = apiKey;
      } else if (apiKey) {
        payload.api_key = apiKey;
      }

      try {
        const res = await fetch('/api/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        if (!res.ok) {
          const err = await res.json();
          alert("Error starting task: " + (err.detail || "Unknown error"));
        }
      } catch (err) {
        alert("Network error starting task: " + err.message);
      }
    }

    async function cancelAgentTask() {
      await fetch('/api/cancel', { method: 'POST' });
    }

    // Keyboard shortcut: Ctrl + Enter
    document.getElementById('task-prompt').addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        startAgentTask();
      }
    });

    // Close modals on Escape
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        toggleStepsModal(false);
        closeAddPresetModal();
        if (isMaximized) toggleMaximizeConsole();
      }
    });

    // Init
    window.addEventListener('DOMContentLoaded', () => {
      lucide.createIcons();
      fetchSystemStatus();
      renderPresets();
      setupSSE();
    });
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the single-page dashboard."""
    return HTMLResponse(content=HTML_TEMPLATE)


def main():
    """Start the FastAPI GUI server and automatically open the default web browser."""
    host = "127.0.0.1"
    port = 8000
    url = f"http://{host}:{port}"

    print(f"\n=======================================================")
    print(f"  Autonomous Browser Agent GUI launching at {url}")
    print(f"=======================================================\n")

    async def open_browser():
        await asyncio.sleep(1.2)
        webbrowser.open(url)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(open_browser())

    config = uvicorn.Config(app, host=host, port=port, log_level="info", loop="asyncio")
    server = uvicorn.Server(config)
    loop.run_until_complete(server.serve())


if __name__ == "__main__":
    main()
