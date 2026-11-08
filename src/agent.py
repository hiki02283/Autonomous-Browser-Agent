"""
Agent Factory and Runner Module.
Initializes and coordinates browser-use Agent with the configured LLM,
headed browser session, custom multi-tab controller, and lifecycle handling.
"""

import os
import sys
import asyncio
from typing import Optional, Dict, Any, List, Callable, Awaitable
from rich.console import Console

from browser_use import Agent, BrowserSession
from browser_use.agent.views import AgentHistoryList
from browser_use.llm.models import ChatGoogle, ChatOpenAI

from src.config import settings, resolve_browser_executable
from src.browser_manager import create_browser_session
from src.custom_controller import create_agent_controller

console = Console()


def resolve_llm(
    model_name: Optional[str] = None,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Any:
    """
    Resolve and instantiate the LLM model instance based on provided credentials or environment variables.
    Supports Google Gemini, OpenAI, Anthropic, Local AI (Ollama / LM Studio),
    and Custom OpenAI-Compatible APIs (OpenRouter, DeepSeek, Groq, Together, etc.).
    """
    req_provider = (provider or settings.default_llm_provider).lower().strip()
    req_model = model_name or settings.default_llm_model

    # 1. Custom OpenAI-Compatible API (OpenRouter, DeepSeek, Groq, Together, etc.)
    if req_provider in ("custom", "custom_openai", "openai_compatible", "openai-compatible", "custom openai-compatible"):
        active_base = (
            (base_url or "").strip()
            or settings.custom_openai_base_url
            or os.getenv("CUSTOM_OPENAI_BASE_URL", "")
        )
        if not active_base:
            raise ValueError(
                "Custom OpenAI-Compatible provider selected but Base URL is empty.\n"
                "Please enter a Base URL (e.g. https://openrouter.ai/api/v1, https://api.deepseek.com/v1, https://api.groq.com/openai/v1)."
            )
        active_key = (
            (api_key or "").strip()
            or settings.custom_openai_api_key
            or os.getenv("CUSTOM_OPENAI_API_KEY", "")
            or "not-needed"
        )
        model = req_model or "deepseek-chat"
        return ChatOpenAI(
            model=model,
            base_url=active_base,
            api_key=active_key,
        )

    # 2. Local AI (Ollama / LM Studio)
    if req_provider in ("local", "ollama", "lmstudio", "local_ai", "local ai"):
        local_base = (
            (base_url or "").strip()
            or settings.local_ai_base_url
            or os.getenv("LOCAL_AI_BASE_URL")
            or "http://localhost:11434/v1"
        )
        local_key = (api_key or "").strip() or os.getenv("LOCAL_AI_API_KEY") or "ollama"
        model = req_model or "llama3.2"
        return ChatOpenAI(
            model=model,
            base_url=local_base,
            api_key=local_key,
        )

    # 3. Google Gemini
    if req_provider in ("google", "gemini"):
        google_key = (api_key or "").strip() or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not google_key:
            raise ValueError(
                "Google Gemini selected but GOOGLE_API_KEY / GEMINI_API_KEY is not provided.\n"
                "Please enter an API key in the UI or add GOOGLE_API_KEY to your .env file."
            )
        model = req_model or "gemini-3.5-flash"
        return ChatGoogle(model=model, api_key=google_key)

    # 4. OpenAI
    if req_provider in ("openai", "chatgpt"):
        openai_key = (api_key or "").strip() or os.getenv("OPENAI_API_KEY")
        if not openai_key:
            raise ValueError(
                "OpenAI selected but OPENAI_API_KEY is not provided.\n"
                "Please enter an API key in the UI or add OPENAI_API_KEY to your .env file."
            )
        model = req_model or "gpt-4o-mini"
        kwargs: Dict[str, Any] = {"model": model, "api_key": openai_key}
        if base_url:
            kwargs["base_url"] = base_url.strip()
        return ChatOpenAI(**kwargs)

    # 5. Anthropic
    if req_provider in ("anthropic", "claude"):
        anthropic_key = (api_key or "").strip() or os.getenv("ANTHROPIC_API_KEY")
        if not anthropic_key:
            raise ValueError("Anthropic selected but ANTHROPIC_API_KEY is not provided.")
        from browser_use.llm.anthropic.chat import ChatAnthropic
        model = req_model or "claude-3-5-sonnet-latest"
        return ChatAnthropic(model=model, api_key=anthropic_key)

    # 6. Fallback if provider was auto or not explicitly matched
    google_key = (api_key or "").strip() or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    openai_key = (api_key or "").strip() or os.getenv("OPENAI_API_KEY")
    anthropic_key = (api_key or "").strip() or os.getenv("ANTHROPIC_API_KEY")

    if google_key:
        return ChatGoogle(model="gemini-3.5-flash", api_key=google_key)
    if openai_key:
        return ChatOpenAI(model="gpt-4o-mini", api_key=openai_key)
    if anthropic_key:
        from browser_use.llm.anthropic.chat import ChatAnthropic
        return ChatAnthropic(model="claude-3-5-sonnet-latest", api_key=anthropic_key)

    raise ValueError(
        "No LLM credentials configured. Please enter an API key or select Local AI / Custom OpenAI-Compatible in the UI, "
        "or set GOOGLE_API_KEY / OPENAI_API_KEY in your .env file."
    )


async def test_llm_connection(
    provider: str,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Send a lightweight probe prompt to test API credentials, network reachability,
    and model availability.
    """
    try:
        llm = resolve_llm(
            model_name=model,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
        )
        from browser_use.llm.messages import UserMessage

        # Send a lightweight ping prompt
        resp = await asyncio.wait_for(
            llm.ainvoke([UserMessage(content="Respond with the single word: pong")]),
            timeout=15.0
        )
        clean_text = getattr(resp, "completion", "") or str(resp)
        model_display = getattr(llm, "model", model or "model")
        return {
            "ok": True,
            "message": f"Connected to {model_display} successfully! ({clean_text.strip()[:30]})"
        }
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "error": f"Connection timed out (15s) while contacting '{provider}'. Check network connection or Base URL."
        }
    except Exception as e:
        err_msg = str(e)
        if "Connection refused" in err_msg or "ConnectError" in err_msg:
            target = base_url or "localhost"
            err_msg = f"Connection refused at {target}. Ensure your local server or API endpoint is running."
        elif "401" in err_msg or "unauthorized" in err_msg.lower():
            err_msg = "Invalid API Key (HTTP 401 Unauthorized). Please check your key."
        elif "404" in err_msg:
            err_msg = f"Model or endpoint not found (HTTP 404). Check model name '{model}' or Base URL."
        return {
            "ok": False,
            "error": err_msg
        }


async def fetch_available_models(
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Query the given provider's API for available chat/completion models.
    Filters and sorts with recommended fast/flash models on top.
    """
    req_provider = (provider or settings.default_llm_provider).lower().strip()

    # 1. Google Gemini
    if req_provider in ("google", "gemini"):
        active_key = (api_key or "").strip() or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
        fallback_google = ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-flash-latest", "gemini-2.5-pro"]
        if not active_key:
            return {
                "ok": False,
                "error": "Google API key is missing. Please enter an API key or configure GOOGLE_API_KEY in .env.",
                "models": fallback_google,
                "recommended": "gemini-3.5-flash",
            }
        try:
            from google import genai
            client = genai.Client(api_key=active_key)
            raw_models = []
            for m in client.models.list():
                actions = getattr(m, "supported_actions", []) or []
                if "generateContent" in actions:
                    clean = m.name.replace("models/", "")
                    if not any(x in clean for x in ["embedding", "aqa", "tts", "imagen", "realtime", "clip", "preview-tts", "transcribe"]):
                        raw_models.append(clean)

            def google_model_score(name: str) -> tuple:
                tier = 3
                is_flash = "flash" in name
                is_pro = "pro" in name and not is_flash
                is_gemma = "gemma" in name
                if is_flash:
                    tier = 0
                elif is_pro:
                    tier = 1
                elif is_gemma:
                    tier = 2
                priority_map = {
                    "gemini-3.5-flash": 0,
                    "gemini-3.8-flash": 1,
                    "gemini-3.7-flash": 2,
                    "gemini-3.5-flash-lite": 3,
                    "gemini-flash-latest": 4,
                    "gemini-3-flash-preview": 5,
                    "gemini-2.5-flash": 6,
                    "gemini-2.5-flash-lite": 7,
                    "gemini-2.0-flash": 8,
                }
                prio = priority_map.get(name, 50)
                return (tier, prio, name)

            sorted_models = sorted(raw_models, key=google_model_score)
            rec = "gemini-3.5-flash" if "gemini-3.5-flash" in sorted_models else ("gemini-3.8-flash" if "gemini-3.8-flash" in sorted_models else (sorted_models[0] if sorted_models else "gemini-3.5-flash"))
            return {"ok": True, "models": sorted_models, "recommended": rec}
        except Exception as e:
            return {
                "ok": False,
                "error": f"Failed to fetch Google models: {str(e)}",
                "models": fallback_google,
                "recommended": "gemini-3.5-flash",
            }

    # 2. OpenAI
    if req_provider in ("openai", "chatgpt"):
        active_key = (api_key or "").strip() or os.getenv("OPENAI_API_KEY") or ""
        fallback_openai = ["gpt-4o-mini", "gpt-4o", "gpt-5-mini", "o3-mini", "o1-mini"]
        if not active_key:
            return {
                "ok": False,
                "error": "OpenAI API key is missing. Please enter an API key or configure OPENAI_API_KEY in .env.",
                "models": fallback_openai,
                "recommended": "gpt-4o-mini",
            }
        try:
            import openai
            client = openai.AsyncOpenAI(api_key=active_key)
            res = await client.models.list()
            raw = [m.id for m in res.data if hasattr(m, "id")]

            def openai_score(name: str) -> tuple:
                if any(x in name for x in ["embedding", "tts", "whisper", "moderation", "realtime", "transcribe", "babbage", "davinci", "dall-e"]):
                    return (999, 999, name)
                priority_map = {
                    "gpt-4o-mini": 0,
                    "gpt-4o": 1,
                    "gpt-5-mini": 2,
                    "o3-mini": 3,
                    "o1-mini": 4,
                    "o1": 5,
                    "chatgpt-4o-latest": 6,
                    "gpt-4-turbo": 7,
                    "gpt-3.5-turbo": 8,
                }
                prio = priority_map.get(name, 50)
                tier = 0 if prio < 50 else (1 if ("gpt" in name or name.startswith("o1") or name.startswith("o3") or name.startswith("o4")) else 2)
                return (tier, prio, name)

            filtered = [m for m in sorted(raw, key=openai_score) if openai_score(m)[0] < 999]
            rec = "gpt-4o-mini" if "gpt-4o-mini" in filtered else (filtered[0] if filtered else "gpt-4o-mini")
            return {"ok": True, "models": filtered, "recommended": rec}
        except Exception as e:
            return {
                "ok": False,
                "error": f"Failed to fetch OpenAI models: {str(e)}",
                "models": fallback_openai,
                "recommended": "gpt-4o-mini",
            }

    # 3. Custom OpenAI-Compatible (OpenRouter, DeepSeek, Groq, Together, etc.)
    if req_provider in ("custom", "custom_openai", "openai_compatible", "openai-compatible", "custom openai-compatible"):
        active_base = (base_url or "").strip() or settings.custom_openai_base_url or os.getenv("CUSTOM_OPENAI_BASE_URL", "")
        active_key = (api_key or "").strip() or settings.custom_openai_api_key or os.getenv("CUSTOM_OPENAI_API_KEY", "") or "not-needed"
        fallback_custom = ["deepseek-chat", "llama-3.3-70b-versatile", "claude-3-5-sonnet"]
        if not active_base:
            return {
                "ok": False,
                "error": "Custom OpenAI-Compatible Base URL is required to fetch models.",
                "models": fallback_custom,
                "recommended": "deepseek-chat",
            }
        try:
            import openai
            client = openai.AsyncOpenAI(api_key=active_key, base_url=active_base.rstrip("/"))
            res = await client.models.list()
            raw = [m.id for m in res.data if hasattr(m, "id")]
            filtered = [m for m in raw if not any(x in m for x in ["embedding", "tts", "whisper", "moderation"])]
            models = filtered if filtered else raw
            rec = models[0] if models else "deepseek-chat"
            return {"ok": True, "models": models, "recommended": rec}
        except Exception as e:
            return {
                "ok": False,
                "error": f"Failed to fetch custom models: {str(e)}",
                "models": fallback_custom,
                "recommended": "deepseek-chat",
            }

    # 4. Local AI (Ollama / LM Studio)
    if req_provider in ("local", "ollama", "lm_studio", "lmstudio"):
        active_base = (base_url or "").strip() or settings.local_ai_base_url or os.getenv("LOCAL_AI_BASE_URL", "http://localhost:11434/v1")
        clean_base = active_base.rstrip("/")
        models = []
        import httpx
        # Attempt 1: /v1/models
        try:
            models_url = f"{clean_base}/models" if not clean_base.endswith("/models") else clean_base
            async with httpx.AsyncClient(timeout=4.0) as http_client:
                resp = await http_client.get(models_url)
                if resp.status_code == 200:
                    d = resp.json()
                    if "data" in d and isinstance(d["data"], list):
                        models = [m["id"] for m in d["data"] if "id" in m]
        except Exception:
            pass

        # Attempt 2: Ollama /api/tags
        if not models:
            root_url = clean_base[:-3] if clean_base.endswith("/v1") else clean_base
            try:
                async with httpx.AsyncClient(timeout=4.0) as http_client:
                    resp = await http_client.get(f"{root_url}/api/tags")
                    if resp.status_code == 200:
                        d = resp.json()
                        if "models" in d and isinstance(d["models"], list):
                            models = [m["name"] for m in d["models"] if "name" in m]
            except Exception:
                pass

        if models:
            return {"ok": True, "models": models, "recommended": models[0]}
        else:
            return {
                "ok": False,
                "error": f"Could not reach Local AI endpoint at {active_base}. Showing standard local models.",
                "models": ["llama3.2", "qwen2.5", "mistral", "deepseek-r1"],
                "recommended": "llama3.2",
            }

    # 5. Anthropic
    if req_provider in ("anthropic", "claude"):
        active_key = (api_key or "").strip() or os.getenv("ANTHROPIC_API_KEY") or ""
        fallback_anthropic = ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"]
        if not active_key:
            return {
                "ok": False,
                "error": "Anthropic API key is missing. Showing default models.",
                "models": fallback_anthropic,
                "recommended": "claude-3-5-sonnet-latest",
            }
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=active_key)
            res = await client.models.list()
            raw = [m.id for m in res.data if hasattr(m, "id")]
            rec = "claude-3-5-sonnet-latest" if "claude-3-5-sonnet-latest" in raw else (raw[0] if raw else "claude-3-5-sonnet-latest")
            return {"ok": True, "models": raw, "recommended": rec}
        except Exception as e:
            return {
                "ok": False,
                "error": f"Failed to fetch Anthropic models: {str(e)}",
                "models": fallback_anthropic,
                "recommended": "claude-3-5-sonnet-latest",
            }

    # Unknown fallback
    return {
        "ok": False,
        "error": f"Unknown provider: {provider}",
        "models": ["gemini-3.5-flash", "gpt-4o-mini", "llama3.2"],
        "recommended": "gemini-3.5-flash",
    }



async def run_autonomous_task(
    task: str,
    headless: Optional[bool] = None,
    browser_type: Optional[str] = None,
    custom_executable_path: Optional[str] = None,
    cdp_url: Optional[str] = None,
    model_name: Optional[str] = None,
    llm_provider: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_steps: int = 30,
    on_step_callback: Optional[Callable[[int, str, Optional[str]], Awaitable[None]]] = None,
    on_human_intervention: Optional[Callable[[str, int], Awaitable[None]]] = None,
    resume_event: Optional[asyncio.Event] = None,
    agent_ref_callback: Optional[Callable[[Agent], None]] = None,
) -> Dict[str, Any]:
    """
    Execute an autonomous browser task from start to finish.
    Initializes a headed browser session (by default), sets up custom multi-tab tools,
    and returns a structured execution summary.
    """
    llm = resolve_llm(
        model_name=model_name,
        provider=llm_provider,
        api_key=api_key,
        base_url=base_url,
    )
    resolved = resolve_browser_executable(
        preferred_type=browser_type or settings.browser_type,
        custom_path=custom_executable_path or settings.browser_executable_path
    )
    is_headless = headless if headless is not None else settings.headless

    browser_session = create_browser_session(
        headless=is_headless,
        browser_type=browser_type,
        custom_executable_path=custom_executable_path,
        cdp_url=cdp_url,
    )

    controller = create_agent_controller(
        on_human_intervention=on_human_intervention,
        resume_event=resume_event,
    )

    # Step callback bridge
    async def _handle_step(state: Any, output: Any, step_number: int):
        action_summary = "Processing next step..."
        thought = ""
        try:
            if hasattr(output, "current_state") and output.current_state:
                thought = getattr(output.current_state, "thought", "") or ""
            if hasattr(output, "action") and output.action:
                action_summary = str(output.action)
            elif thought:
                action_summary = thought
        except Exception:
            pass

        if on_step_callback:
            try:
                await on_step_callback(step_number, action_summary, thought)
            except Exception:
                pass

    agent = Agent(
        task=task,
        llm=llm,
        browser_session=browser_session,
        controller=controller,
        use_vision=True,
        register_new_step_callback=_handle_step,
    )

    if agent_ref_callback:
        try:
            agent_ref_callback(agent)
        except Exception:
            pass

    try:
        history: AgentHistoryList = await agent.run(max_steps=max_steps)
        final_result = history.final_result()
        is_done = history.is_done()
        urls_visited = history.urls()
        total_steps = history.number_of_steps()

        return {
            "success": is_done,
            "task": task,
            "final_result": final_result or "Task execution finished.",
            "total_steps": total_steps,
            "urls_visited": urls_visited,
            "browser_info": {
                "browser_type": resolved["browser_type"],
                "executable_path": resolved["executable_path"],
                "headless": is_headless,
            },
            "errors": history.errors() if history.has_errors() else [],
        }
    finally:
        try:
            await browser_session.stop()
        except Exception:
            pass
