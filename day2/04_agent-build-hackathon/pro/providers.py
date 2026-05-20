"""Provider abstraction — Claude is the concrete; OpenAI + Gemini are stubs.

A thin facade so the agent / critique / draft / rerank stages can be re-
pointed at a different LLM vendor without touching the call sites.

Today, only Anthropic is implemented (matches the workshop API key). The
abstract interface + the OpenAI/Gemini stubs make it a config flip — set
PRO_PROVIDER=openai and supply OPENAI_API_KEY, the stub turns into a real
client after replacing two methods.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ProviderResponse:
    """Vendor-neutral response shape consumed by the rest of pro/."""
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str
    raw: object = None  # vendor-specific original response


class LLMProvider(ABC):
    """Each provider exposes two operations: simple text generation + tool-
    use call. Streaming, vision, etc. can be added incrementally."""

    name: str = "abstract"

    @abstractmethod
    def generate(self, *, system: str, user: str, model: str,
                  max_tokens: int) -> ProviderResponse:
        ...

    @abstractmethod
    def generate_tool_call(self, *, system: str, user: str, model: str,
                             tool: dict, max_tokens: int) -> dict:
        """Force a tool call and return the structured input dict."""
        ...


# ---------------------------------------------------------------------------
# Anthropic — the real one
# ---------------------------------------------------------------------------

class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, timeout: float = 600.0):
        import anthropic
        self._client = anthropic.Anthropic(timeout=timeout)

    def generate(self, *, system, user, model, max_tokens):
        r = self._client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in r.content if b.type == "text")
        return ProviderResponse(
            text=text,
            input_tokens=r.usage.input_tokens,
            output_tokens=r.usage.output_tokens,
            stop_reason=r.stop_reason,
            raw=r,
        )

    def generate_tool_call(self, *, system, user, model, tool, max_tokens):
        r = self._client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": user}],
        )
        tu = next((b for b in r.content if b.type == "tool_use"), None)
        if tu is None:
            raise RuntimeError(f"{self.name}: model did not call the forced tool")
        return tu.input


# ---------------------------------------------------------------------------
# OpenAI / Gemini stubs — interface only.
# ---------------------------------------------------------------------------

class OpenAIProvider(LLMProvider):
    """Stub. Configure OPENAI_API_KEY + uncomment the body to enable."""
    name = "openai"

    def __init__(self, timeout: float = 600.0):
        if not os.environ.get("OPENAI_API_KEY"):
            raise NotImplementedError(
                "OpenAIProvider is stubbed. Set OPENAI_API_KEY + replace this "
                "module's `generate` / `generate_tool_call` with the OpenAI SDK."
            )

    def generate(self, **kwargs):
        raise NotImplementedError("Wire to openai.chat.completions.create + map to ProviderResponse")

    def generate_tool_call(self, **kwargs):
        raise NotImplementedError("Wire to openai tools=[{...}] + tool_choice='required'")


class GeminiProvider(LLMProvider):
    """Stub. Configure GEMINI_API_KEY + uncomment the body to enable."""
    name = "gemini"

    def __init__(self, timeout: float = 600.0):
        if not os.environ.get("GEMINI_API_KEY"):
            raise NotImplementedError(
                "GeminiProvider is stubbed. Set GEMINI_API_KEY + replace this "
                "module's `generate` / `generate_tool_call` with the Google SDK."
            )

    def generate(self, **kwargs):
        raise NotImplementedError("Wire to google.generativeai")

    def generate_tool_call(self, **kwargs):
        raise NotImplementedError("Wire to Gemini function-calling API")


def get_provider(name: str | None = None) -> LLMProvider:
    """Factory. Honors PRO_PROVIDER env or explicit argument; defaults to anthropic."""
    chosen = (name or os.environ.get("PRO_PROVIDER") or "anthropic").lower()
    if chosen == "anthropic":
        return AnthropicProvider()
    if chosen == "openai":
        return OpenAIProvider()
    if chosen == "gemini":
        return GeminiProvider()
    raise ValueError(f"Unknown provider: {chosen}")
