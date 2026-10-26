import logging
import httpx

from backend.generation.prompts.prompt_result import PromptResult
from backend.generation.providers.base_provider import BaseProvider
from backend.generation.providers.provider_result import ProviderResult

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseProvider):
    """
    Anthropic Claude Provider for VoxCode (Claude 3.7 Sonnet / Claude 3.5).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-7-sonnet-20250219",
    ):
        super().__init__(
            provider_name="anthropic",
            model=model,
        )
        self.api_key = api_key

    def generate(self, prompt: PromptResult) -> ProviderResult:
        if not self.api_key:
            return ProviderResult(
                response="[Anthropic Provider Error: ANTHROPIC_API_KEY is not configured]",
                model_name=self.model,
                provider_name=self.provider_name,
                prompt_size=prompt.prompt_size,
                completion_size=0,
                total_size=prompt.prompt_size,
            )

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "system": prompt.system_prompt,
            "messages": [
                {"role": "user", "content": prompt.user_prompt},
            ],
            "max_tokens": 4096,
        }

        with httpx.Client(timeout=60.0) as client:
            resp = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            if resp.status_code != 200:
                logger.error(f"Anthropic API error ({resp.status_code}): {resp.text}")
                content = f"Error from Anthropic API: {resp.text}"
            else:
                data = resp.json()
                content = "".join([block.get("text", "") for block in data.get("content", [])])

        return ProviderResult(
            response=content,
            model_name=self.model,
            provider_name=self.provider_name,
            prompt_size=prompt.prompt_size,
            completion_size=len(content),
            total_size=prompt.prompt_size + len(content),
        )
