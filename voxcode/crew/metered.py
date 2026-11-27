"""Give each agent its own token-metered view of the model."""

from __future__ import annotations

from voxcode.generation.providers.types import Usage


class MeteredProvider:
    """Delegates to a provider and books every call's tokens to one agent."""

    def __init__(self, provider, trace, agent: str):
        self._provider, self._trace, self._agent = provider, trace, agent

    def __getattr__(self, name):
        return getattr(self._provider, name)

    def generate(self, prompt):
        result = self._provider.generate(prompt)
        self._trace.add_usage(self._agent, self._provider.model, result.input_tokens, result.output_tokens)
        return result

    def chat(self, system, messages, tools=None):
        turn = self._provider.chat(system, messages, tools)
        self._trace.add_usage(
            self._agent, self._provider.model, turn.usage.input_tokens, turn.usage.output_tokens
        )
        return turn

    def stream(self, prompt, usage: Usage | None = None):
        own = usage or Usage()
        yield from self._provider.stream(prompt, own)
        self._trace.add_usage(self._agent, self._provider.model, own.input_tokens, own.output_tokens)


class AgentRegistry:
    """The real model registry, except get_provider() returns a metered provider."""

    def __init__(self, registry, provider):
        self._registry, self._provider = registry, provider

    def __getattr__(self, name):
        return getattr(self._registry, name)

    def get_provider(self, name=None):
        return self._provider

    def get_cascade_provider(self):
        return None  # crew agents use the main model
