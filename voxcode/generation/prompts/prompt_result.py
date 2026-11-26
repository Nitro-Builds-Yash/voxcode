from pydantic import BaseModel


class PromptResult(BaseModel):
    system_prompt: str
    user_prompt: str
    full_prompt: str
    prompt_size: int
    # When set, user_prompt == context + "\n\n" + task. Providers that support
    # prompt caching cache the (large, repeated) context separately.
    context: str = ""
    task: str = ""
