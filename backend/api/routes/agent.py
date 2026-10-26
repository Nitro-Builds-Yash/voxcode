import os
import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.agent.agent_request import AgentRequest
from backend.agent.pipeline import AgentPipeline
from backend.core.model_registry import ModelRegistry
from backend.agent.memory.memory_manager import MemoryManager
from backend.voice.emotion.emotion_analyzer import EmotionAnalyzer, EmotionProfile
from backend.voice.tts.tts_service import TTSService
from backend.generation.prompts.vox_prompts import VOX_AGENT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/agent",
    tags=["agent"],
)


class AgentQueryRequest(BaseModel):
    query: str
    repository_path: Optional[str] = "."
    session_id: Optional[str] = "default_session"
    auto_tts: Optional[bool] = False


class AgentQueryResponse(BaseModel):
    query: str
    summary: str
    diff: Optional[str] = None
    files_modified: list[str] = []
    status: str
    emotion: Optional[Dict[str, Any]] = None
    vox_voice_feedback: Optional[str] = None


registry = ModelRegistry()
memory = MemoryManager()
emotion_analyzer = EmotionAnalyzer()
tts_service = TTSService()


@router.post("/query", response_model=AgentQueryResponse)
def query_agent(payload: AgentQueryRequest):
    """
    Executes a voice or text instruction with emotional state analysis and adaptive Vox assistance.
    """
    # 1. Analyze developer sentiment and tone
    emotion: EmotionProfile = emotion_analyzer.analyze(payload.query)

    # 2. Inject Vox contextual adaptive directive
    vox_prefix = (
        f"\n\n[VOXCODE ADAPTIVE ASSISTANCE DIRECTIVE]\n"
        f"Detected User State: {emotion.primary_emotion.upper()} (Intensity: {emotion.intensity:.1f}, Sentiment: {emotion.sentiment})\n"
        f"Observation: {emotion.tone_description}\n"
        f"Adaptation: {emotion.vox_adaptation}\n"
    )

    try:
        repo_name = os.path.basename(os.path.abspath(payload.repository_path)) or "default_repo"
        pipeline = AgentPipeline(
            repository_name=repo_name,
            registry=registry,
            memory=memory,
        )

        instruction = f"{payload.query}\n{vox_prefix}"

        request = AgentRequest(
            instruction=instruction,
            session_id=payload.session_id,
        )

        response = pipeline.run(request)
        summary = getattr(response, "message", "Task completed.")
        diff = getattr(response, "diff", None)
        files_modified = getattr(response, "files_modified", [])

        # Formulate a natural spoken summary
        voice_line = summary
        if len(summary) > 200:
            if emotion.primary_emotion == "frustrated":
                voice_line = "I've located the issue and prepared a fix. Applying the patch now."
            elif emotion.primary_emotion == "urgent":
                voice_line = "Patch ready. Applying hotfix to the affected files."
            elif emotion.primary_emotion == "fatigued":
                voice_line = "Done. I've taken care of the changes for you."
            else:
                voice_line = f"Done. Updated {len(files_modified)} files in the repository."

        if payload.auto_tts:
            tts_service.speak(
                voice_line,
                emotion=emotion.primary_emotion,
                intensity=emotion.intensity,
            )

        return AgentQueryResponse(
            query=payload.query,
            summary=summary,
            diff=diff,
            files_modified=files_modified,
            status="success",
            emotion=emotion.model_dump(),
            vox_voice_feedback=voice_line,
        )

    except Exception as e:
        logger.error(f"VoxCode Agent execution error: {e}")
        fallback_msg = f"Acknowledged query: '{payload.query}'. Notice: {str(e)}"
        return AgentQueryResponse(
            query=payload.query,
            summary=fallback_msg,
            diff=None,
            files_modified=[],
            status="partial_or_error",
            emotion=emotion.model_dump(),
            vox_voice_feedback=fallback_msg,
        )
