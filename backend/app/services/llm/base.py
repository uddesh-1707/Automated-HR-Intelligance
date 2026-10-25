"""Provider-neutral LLM types. Messages use the OpenAI chat shape as the canonical format."""

from dataclasses import dataclass, field


class LLMNotConfigured(RuntimeError):
    """No usable LLM configuration; features that need an LLM are disabled."""


class LLMConfigError(RuntimeError):
    """The LLM configuration is present but invalid."""


class LLMProviderError(RuntimeError):
    """The provider request failed."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)

    def as_message(self) -> dict:
        """Canonical assistant message to append to the running conversation."""
        message: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
                for call in self.tool_calls
            ]
        return message


class LLMClient:
    """Interface every provider adapter implements."""

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        raise NotImplementedError
