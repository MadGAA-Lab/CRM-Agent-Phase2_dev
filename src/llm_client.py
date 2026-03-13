"""Unified LLM client supporting Anthropic (Claude) and OpenAI-compatible APIs."""

import os
import logging

import anthropic
import openai

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Unified LLM client with token tracking.

    Model routing:
    - Planning + SQL generation + Answer synthesis → Claude Sonnet 4
    - Context filtering → Llama 3.3 70B via Nebius (cheaper) or Claude fallback
    - Privacy guard → Rule-based (no LLM)

    Environment variables:
    - ANTHROPIC_API_KEY: For Claude models (primary)
    - NEBIUS_API_KEY: For Llama models via Nebius (cost-optimized)
    - OPENAI_API_KEY: For OpenAI models (fallback)
    """

    NEBIUS_BASE_URL = "https://api.studio.nebius.com/v1"

    def __init__(self):
        self.anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        self.nebius_key = os.environ.get("NEBIUS_API_KEY")
        self.openai_key = os.environ.get("OPENAI_API_KEY")
        self._total_tokens = 0
        self._tool_calls = 0
        self._queries = 0

        # Initialize clients based on available keys
        self._anthropic_client = None
        self._openai_client = None
        self._nebius_client = None

        if self.anthropic_key:
            self._anthropic_client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)
        if self.nebius_key:
            self._nebius_client = openai.AsyncOpenAI(
                api_key=self.nebius_key,
                base_url=self.NEBIUS_BASE_URL,
            )
        if self.openai_key:
            self._openai_client = openai.AsyncOpenAI(api_key=self.openai_key)

    async def call(
        self,
        prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str:
        """
        Call the LLM and return response text.

        Model selection priority:
        1. If Anthropic key available → use Claude
        2. If Nebius key available → use Llama via Nebius
        3. If OpenAI key available → use GPT
        """
        self._tool_calls += 1
        self._queries += 1

        if self._anthropic_client:
            return await self._call_anthropic(prompt, model, temperature, max_tokens)
        elif self._nebius_client:
            return await self._call_openai_compatible(
                self._nebius_client,
                prompt,
                model or "meta-llama/Llama-3.3-70B-Instruct",
                temperature,
                max_tokens,
            )
        elif self._openai_client:
            return await self._call_openai_compatible(
                self._openai_client,
                prompt,
                model or "gpt-4o",
                temperature,
                max_tokens,
            )
        else:
            raise RuntimeError(
                "No LLM API key configured. Set ANTHROPIC_API_KEY, NEBIUS_API_KEY, or OPENAI_API_KEY."
            )

    async def call_cheap(
        self,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> str:
        """
        Call a cheaper/faster model for simple tasks like context filtering.

        Priority: Nebius (Llama) → OpenAI → Anthropic (with cheaper model)
        """
        self._tool_calls += 1
        self._queries += 1

        if self._nebius_client:
            return await self._call_openai_compatible(
                self._nebius_client,
                prompt,
                "meta-llama/Llama-3.3-70B-Instruct",
                temperature,
                max_tokens,
            )
        elif self._openai_client:
            return await self._call_openai_compatible(
                self._openai_client,
                prompt,
                "gpt-4o-mini",
                temperature,
                max_tokens,
            )
        elif self._anthropic_client:
            return await self._call_anthropic(
                prompt, "claude-sonnet-4-20250514", temperature, max_tokens
            )
        else:
            raise RuntimeError("No LLM API key configured.")

    async def _call_anthropic(
        self,
        prompt: str,
        model: str | None,
        temperature: float,
        max_tokens: int,
    ) -> str:
        model = model or "claude-sonnet-4-20250514"
        response = await self._anthropic_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        # Track tokens
        self._total_tokens += response.usage.input_tokens + response.usage.output_tokens
        return response.content[0].text

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
