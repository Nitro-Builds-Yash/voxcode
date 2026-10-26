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
from backend.generation.prompts.jarvis_prompts import JARVIS_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/agent",
    tags=["agent"],
)


class AgentQueryRequest(BaseModel):
    query: str
    repository_path: Optional[str] = "."
    session_id: Optional[str] = "default_session"
    persona: Optional[str] = "jarvis"  # "jarvis" or "standard"
    auto_tts: Optional[bool] = False


class AgentQueryResponse(BaseModel):
    query: str
    summary: str
    diff: Optional[str] = None
    files_modified: list[str] = []
    status: str
    emotion: Optional[Dict[str, Any]] = None
    jarvis_voice_line: Optional[str] = None


registry = ModelRegistry()
memory = MemoryManager()
emotion_analyzer = EmotionAnalyzer()
tts_service = TTSService()


@router.post("/query", response_model=AgentQueryResponse)
def query_agent(payload: AgentQueryRequest):
    """
    Executes a voice/text query with emotional tone detection and JARVIS agentic persona.
    """
    # 1. Analyze user tone and emotion
    emotion: EmotionProfile = emotion_analyzer.analyze(payload.query)

    # 2. Build JARVIS contextual system prompt injection
    jarvis_prefix = ""
    if payload.persona == "jarvis":
        jarvis_prefix = (
            f"\n\n[USER EMOTIONAL STATE & TONAL DIRECTIVE]\n"
            f"Detected Emotion: {emotion.primary_emotion.upper()} (Intensity: {emotion.intensity:.1f}, Sentiment: {emotion.sentiment})\n"
            f"Context: {emotion.tone_description}\n"
            f"Directive: {emotion.jarvis_directive}\n"
            f"Respond as J.A.R.V.I.S. with calm technological mastery."
        )

    try:
        repo_name = os.path.basename(os.path.abspath(payload.repository_path)) or "default_repo"
        pipeline = AgentPipeline(
            repository_name=repo_name,
            registry=registry,
            memory=memory,
        )

        instruction = payload.query
        if jarvis_prefix:
            instruction = f"{payload.query}\n{jarvis_prefix}"

        request = AgentRequest(
            instruction=instruction,
            session_id=payload.session_id,
        )

        response = pipeline.run(request)
        summary = getattr(response, "message", "Query processed successfully.")
        diff = getattr(response, "diff", None)
        files_modified = getattr(response, "files_modified", [])

        # Generate a spoken voice line tailored for JARVIS
        voice_line = summary
        if len(summary) > 200:
            if emotion.primary_emotion == "frustrated":
                voice_line = "I have identified the failure point, sir. Applying the necessary corrections now."
            elif emotion.primary_emotion == "urgent":
                voice_line = "Deploying hotfix patch immediately, sir. Diagnostics complete."
            elif emotion.primary_emotion == "fatigued":
                voice_line = "I have handled the implementation for you, sir. Review the diff when you are ready."
            else:
                voice_line = f"Task completed, sir. {len(files_modified)} files updated."

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
            jarvis_voice_line=voice_line,
        )

    except Exception as e:
        logger.error(f"Agent pipeline execution error: {e}")
        fallback_msg = f"At your service, sir. I've noted: '{payload.query}'. Diagnostics report: {str(e)}"
        return AgentQueryResponse(
            query=payload.query,
            summary=fallback_msg,
            diff=None,
            files_modified=[],
            status="partial_or_error",
            emotion=emotion.model_dump(),
            jarvis_voice_line=fallback_msg,
        )
