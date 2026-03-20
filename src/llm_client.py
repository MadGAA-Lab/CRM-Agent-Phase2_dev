"""Unified LLM client — any OpenAI-compatible provider for both tiers."""

import os
import logging

import openai

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Unified LLM client with token tracking.

    Two independent tiers — primary (expensive) and cheap — each backed by any
    OpenAI-compatible provider.  Configure each tier with a key and an optional
    base URL; no provider is hard-coded.

    Model routing:
    - Planning + SQL generation + Answer synthesis → primary tier  (call)
    - Context filtering                            → cheap tier    (call_cheap)
    - Privacy guard                                → rule-based (no LLM)

    ── Primary tier ────────────────────────────────────────────────────────────
    OPENAI_PRIMARY_API_KEY  Key for the primary provider
    LLM_PRIMARY_BASE_URL    Optional base URL override
                            e.g. https://api.anthropic.com/v1  (Claude compat)
                            e.g. https://api.groq.com/openai/v1
                            (default: https://api.anthropic.com/v1)
    LLM_PRIMARY_MODEL       Model name  (default: claude-sonnet-4-6)

    ── Cheap tier ──────────────────────────────────────────────────────────────
    OPENAI_CHEAP_API_KEY    Key for the cheap provider (optional — falls back to
                            OPENAI_PRIMARY_API_KEY when not set)
    LLM_CHEAP_BASE_URL      Optional base URL override
                            e.g. https://api.studio.nebius.com/v1
                            e.g. http://localhost:8000/v1  (local vLLM)
                            (default: LLM_PRIMARY_BASE_URL)
    LLM_CHEAP_MODEL         Model name  (default: claude-haiku-4-5)

    Examples
    --------
    # Claude as primary, Nebius/Llama as cheap:
    OPENAI_PRIMARY_API_KEY=sk-ant-…
    LLM_PRIMARY_BASE_URL=https://api.anthropic.com/v1
    LLM_PRIMARY_MODEL=claude-sonnet-4-6
    OPENAI_CHEAP_API_KEY=<nebius-key>
    LLM_CHEAP_BASE_URL=https://api.studio.nebius.com/v1
    LLM_CHEAP_MODEL=claude-haiku-4-5
    """

    def __init__(self):
        self.openai_key = os.environ.get("OPENAI_PRIMARY_API_KEY")
        self.openai_cheap_key = os.environ.get("OPENAI_CHEAP_API_KEY")

        # Base URL overrides — primary falls back to Claude's compat endpoint;
        # cheap falls back to primary so both tiers share the same provider by default.
        self.primary_base_url = os.environ.get("LLM_PRIMARY_BASE_URL", "https://api.anthropic.com/v1/")
        self.cheap_base_url = os.environ.get("LLM_CHEAP_BASE_URL", self.primary_base_url)

        # Model configuration via env vars
        self.primary_model = os.environ.get("LLM_PRIMARY_MODEL", "claude-sonnet-4-6")
        # Cheap model default: haiku when pointing at Anthropic, otherwise mirror primary model
        _cheap_model_default = (
            "claude-haiku-4-5"
            if self.cheap_base_url == "https://api.anthropic.com/v1/"
            else self.primary_model
        )
        self.cheap_model = os.environ.get("LLM_CHEAP_MODEL", _cheap_model_default)

        self._total_tokens = 0
        self._tool_calls = 0
        self._queries = 0

        self._primary_client = None   # expensive tier
        self._cheap_client = None     # cost-optimised tier

        if self.openai_key:
            self._primary_client = openai.AsyncOpenAI(
                api_key=self.openai_key,
                base_url=self.primary_base_url,
            )

        # Cheap client: use dedicated cheap key if provided, otherwise fall back
        # to the primary key so that LLM_CHEAP_BASE_URL / LLM_CHEAP_MODEL still work
        # even when OPENAI_CHEAP_API_KEY is not set.
        cheap_key = self.openai_cheap_key or self.openai_key
        if cheap_key:
            self._cheap_client = openai.AsyncOpenAI(
                api_key=cheap_key,
                base_url=self.cheap_base_url,  # Same endpoint as primary
            )

    async def call(
        self,
        prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str:
        """
        Call the primary (expensive) tier and return response text.

        Falls back to the cheap tier if no primary key is configured.
        """
        self._tool_calls += 1
        self._queries += 1

        if self._primary_client:
            return await self._call_openai_compatible(
                self._primary_client,
                prompt,
                model or self.primary_model,
                temperature,
                max_tokens,
            )
        elif self._cheap_client:
            return await self._call_openai_compatible(
                self._cheap_client,
                prompt,
                model or self.cheap_model,
                temperature,
                max_tokens,
            )
        else:
            raise RuntimeError(
                "No LLM API key configured. Set OPENAI_PRIMARY_API_KEY or OPENAI_CHEAP_API_KEY."
            )

    async def call_cheap(
        self,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> str:
        """
        Call the cheap tier for simple tasks like context filtering.

        Falls back to the primary tier if no cheap key is configured.
        Configure via OPENAI_CHEAP_API_KEY + LLM_CHEAP_BASE_URL.
        """
        self._tool_calls += 1
        self._queries += 1

        if self._cheap_client:
            return await self._call_openai_compatible(
                self._cheap_client,
                prompt,
                self.cheap_model,
                temperature,
                max_tokens,
            )
        elif self._primary_client:
            return await self._call_openai_compatible(
                self._primary_client,
                prompt,
                self.cheap_model,
                temperature,
                max_tokens,
            )
        else:
            raise RuntimeError("No LLM API key configured.")

    async def _call_openai_compatible(
        self,
        client: openai.AsyncOpenAI,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        response = await client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # Track tokens
        if response.usage:
            self._total_tokens += response.usage.total_tokens
        return response.choices[0].message.content or ""

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    @property
    def queries(self) -> int:
        return self._queries

    def reset_metrics(self):
        """Reset metrics counters for a new task."""
        self._total_tokens = 0
        self._tool_calls = 0
        self._queries = 0
