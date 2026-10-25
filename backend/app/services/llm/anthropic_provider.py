"""Adapter for Anthropic's Messages API (plain HTTPS; no extra SDK dependency)."""

import json
import logging

import httpx

from app.services.llm.base import LLMClient, LLMProviderError, LLMResponse, ToolCall

logger = logging.getLogger(__name__)
DEFAULT_BASE_URL = "https://api.anthropic.com"


def to_anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Translate canonical (OpenAI-shaped) messages into Anthropic's system string and message list."""
    system_parts: list[str] = []
    converted: list[dict] = []

    def push(role: str, blocks: list[dict]) -> None:
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})

    for message in messages:
        role = message.get("role")
        content = message.get("content") or ""
        if role == "system":
            system_parts.append(content)
        elif role == "user":
            push("user", [{"type": "text", "text": content}])
        elif role == "assistant":
            blocks = [{"type": "text", "text": content}] if content else []
            for call in message.get("tool_calls") or []:
                try:
                    arguments = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                blocks.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": arguments})
            if blocks:
                push("assistant", blocks)
        elif role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": content}])
    return "\n\n".join(system_parts), converted


class AnthropicClient(LLMClient):
    def __init__(self, *, api_key: str, model: str, base_url: str | None, temperature: float,
                 max_tokens: int, timeout: float) -> None:
        self.api_key = api_key
        self.model = model
        self.url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/v1/messages"
        self.temperature = min(temperature, 1.0)
        self.max_tokens = max_tokens
        self.timeout = timeout

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        system, converted = to_anthropic_messages(messages)
        body: dict = {"model": self.model, "max_tokens": self.max_tokens, "temperature": self.temperature, "messages": converted}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {
                    "name": item["function"]["name"],
                    "description": item["function"].get("description", ""),
                    "input_schema": item["function"].get("parameters", {"type": "object", "properties": {}}),
                }
                for item in tools
            ]
        try:
            response = httpx.post(
                self.url,
                json=body,
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Anthropic request failed (%s)", type(exc).__name__)
            raise LLMProviderError("The configured LLM provider could not complete the request.") from exc
        text = "".join(block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text")
        calls = [
            ToolCall(id=block["id"], name=block["name"], arguments=json.dumps(block.get("input", {})))
            for block in payload.get("content", [])
            if block.get("type") == "tool_use"
        ]
        return LLMResponse(content=text.strip(), tool_calls=calls)
