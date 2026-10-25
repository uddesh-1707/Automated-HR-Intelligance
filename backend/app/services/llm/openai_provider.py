"""Adapter for OpenAI and any OpenAI-compatible endpoint (Azure OpenAI proxies, Ollama, vLLM, OpenRouter, ...)."""

import logging

from app.services.llm.base import LLMClient, LLMProviderError, LLMResponse, ToolCall

logger = logging.getLogger(__name__)


class OpenAICompatibleClient(LLMClient):
    def __init__(self, *, api_key: str | None, model: str, base_url: str | None, temperature: float,
                 max_tokens: int, timeout: float) -> None:
        from openai import OpenAI

        options = {"api_key": api_key or "not-needed", "timeout": timeout}
        if base_url:
            options["base_url"] = base_url
        self.client = OpenAI(**options)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        from openai import OpenAIError

        arguments = {"model": self.model, "messages": messages, "temperature": self.temperature, "max_tokens": self.max_tokens}
        if tools:
            arguments.update(tools=tools, tool_choice="auto")
        try:
            response = self.client.chat.completions.create(**arguments)
        except OpenAIError as exc:
            logger.warning("OpenAI-compatible request failed (%s)", type(exc).__name__)
            raise LLMProviderError("The configured LLM provider could not complete the request.") from exc
        message = response.choices[0].message
        calls = [
            ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments or "{}")
            for call in (message.tool_calls or [])
        ]
        return LLMResponse(content=(message.content or "").strip(), tool_calls=calls)
