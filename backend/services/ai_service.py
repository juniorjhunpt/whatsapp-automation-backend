import httpx
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Cost per 1K tokens (approximate, in USD)
COSTS = {
    "openai": {"gpt-4o": 0.005, "gpt-4o-mini": 0.00015, "gpt-3.5-turbo": 0.0005},
    "anthropic": {"claude-sonnet-4-20250514": 0.003, "claude-haiku-4-5": 0.00025},
    "deepseek": {"deepseek-chat": 0.00014},
    "groq": {"llama-3.3-70b-versatile": 0.0, "mixtral-8x7b-32768": 0.0},
    "minimax": {"abab6.5s-chat": 0.001},
    "openrouter": {"default": 0.001},
}

PROVIDER_URLS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "minimax": "https://api.minimax.chat/v1/text/chatcompletion_v2",
}

async def get_ai_response(
    provider: str,
    model: str,
    api_key: str,
    system_prompt: str,
    message_history: list[dict],
    user_message: str,
) -> dict:
    """
    Unified AI call. Returns {"response": str, "tokens_used": int, "cost": float}.
    Raises an exception on failure (caller handles retry).
    """
    start = time.monotonic()
    messages = message_history + [{"role": "user", "content": user_message}]

    if provider == "anthropic":
        result = await _anthropic_call(api_key, model, system_prompt, messages)
    elif provider in PROVIDER_URLS:
        result = await _openai_compat_call(PROVIDER_URLS[provider], api_key, model, system_prompt, messages)
    elif provider == "openrouter":
        result = await _openai_compat_call(
            "https://openrouter.ai/api/v1/chat/completions",
            api_key,
            model,
            system_prompt,
            messages,
            extra_headers={"HTTP-Referer": "https://wahub.local", "X-Title": "WA Hub"},
        )
    else:
        raise ValueError(f"Unknown AI provider: {provider}")

    elapsed_ms = int((time.monotonic() - start) * 1000)
    tokens = result.get("tokens_used", 0)
    cost_per_1k = COSTS.get(provider, {}).get(model, 0.001)
    cost = (tokens / 1000) * cost_per_1k

    return {
        "response": result["response"],
        "tokens_used": tokens,
        "cost": cost,
        "response_time_ms": elapsed_ms,
    }

async def _openai_compat_call(
    url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    extra_headers: Optional[dict] = None,
) -> dict:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)

    body = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": 1024,
        "temperature": 0.7,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    text = data["choices"][0]["message"]["content"].strip()
    tokens = data.get("usage", {}).get("total_tokens", 0)
    return {"response": text, "tokens_used": tokens}

async def _anthropic_call(api_key: str, model: str, system_prompt: str, messages: list[dict]) -> dict:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": messages,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post("https://api.anthropic.com/v1/messages", json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    text = data["content"][0]["text"].strip()
    tokens = data.get("usage", {}).get("input_tokens", 0) + data.get("usage", {}).get("output_tokens", 0)
    return {"response": text, "tokens_used": tokens}

async def test_api_key(provider: str, model: str, api_key: str) -> dict:
    try:
        result = await get_ai_response(
            provider=provider,
            model=model,
            api_key=api_key,
            system_prompt="You are a helpful assistant.",
            message_history=[],
            user_message="Say 'OK' only.",
        )
        return {"ok": True, "response": result["response"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
