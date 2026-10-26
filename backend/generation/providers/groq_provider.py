import logging
import httpx

from backend.generation.prompts.prompt_result import PromptResult
from backend.generation.providers.base_provider import BaseProvider
from backend.generation.providers.provider_result import ProviderResult

logger = logging.getLogger(__name__)


class GroqProvider(BaseProvider):
    """
    Groq Ultra-Fast Inference Provider for VoxCode (using OpenAI-compatible endpoints).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
    ):
        super().__init__(
            provider_name="groq",
            model=model,
        )
        self.api_key = api_key

    def generate(self, prompt: PromptResult) -> ProviderResult:
        if not self.api_key:
            return ProviderResult(
                response="[Groq Provider Error: GROQ_API_KEY is not configured]",
                model_name=self.model,
                provider_name=self.provider_name,
                prompt_size=prompt.prompt_size,
                completion_size=0,
                total_size=prompt.prompt_size,
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt.system_prompt},
                {"role": "user", "content": prompt.user_prompt},
            ],
            "temperature": 0.2,
        }

        with httpx.Client(timeout=30.0) as client:
            resp = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
            if resp.status_code != 200:
                logger.error(f"Groq API error ({resp.status_code}): {resp.text}")
                content = f"Error from Groq API: {resp.text}"
            else:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]

        return ProviderResult(
            response=content,
            model_name=self.model,
            provider_name=self.provider_name,
            prompt_size=prompt.prompt_size,
            completion_size=len(content),
            total_size=prompt.prompt_size + len(content),
        )
