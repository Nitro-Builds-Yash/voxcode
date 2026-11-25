from backend.generation.context.context_result import ContextResult
from backend.generation.prompts.prompt_result import PromptResult
from backend.generation.prompts.system_prompts import REPOSITORY_RULES, build_system_prompt


class PromptBuilder:
    def build(
        self,
        context: ContextResult,
        directive: str | None = None,
        rules: str = REPOSITORY_RULES,
        task: str = "Answer the user's request using the repository context above.",
    ) -> PromptResult:
        system_prompt = build_system_prompt(rules, directive)
        user_prompt = f"{context.context}\n\n{task}"
        return PromptResult(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            full_prompt=f"{system_prompt}\n\n{user_prompt}",
            prompt_size=len(system_prompt) + len(user_prompt),
        )
