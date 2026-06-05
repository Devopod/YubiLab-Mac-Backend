"""
YubiLab AI Agent Core - FULLY SYNCHRONOUS.
Think → Plan → Execute → Verify → Iterate
NO asyncio - compatible with eventlet + gunicorn on macOS.
"""

import os
import json
import uuid
import time
import shutil
from typing import Dict, List, Optional, Callable
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

from .context_manager import ContextManager, ProjectContext, CodePart
from .planner import AgentPlanner, AgentPlan, AgentStep, StepType, StepStatus
from .executor import AgentExecutor, ExecutionResult


class AgentState(Enum):
    IDLE = "idle"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class AgentSession:
    session_id: str
    user_id: str
    workspace_path: str
    task: str
    state: AgentState = AgentState.IDLE
    plan: Optional[AgentPlan] = None
    project_context: Optional[ProjectContext] = None
    conversation_history: List[Dict] = field(default_factory=list)
    activity_log: List[Dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    current_part: int = 0
    total_parts: int = 0
    stream_callback: Optional[Callable] = None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "workspace_path": self.workspace_path,
            "task": self.task,
            "state": self.state.value,
            "plan": self.plan.to_dict() if self.plan else None,
            "project_context": self.project_context.to_dict() if self.project_context else None,
            "current_part": self.current_part,
            "total_parts": self.total_parts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "activity_count": len(self.activity_log),
        }


AGENT_SYSTEM_PROMPT = """You are YubiLab AI Agent - a fully autonomous software engineer, like Devin, Replit Agent, and z.ai.

You can:
- Analyze entire codebases and understand project structure
- Plan complex multi-step tasks autonomously
- Write, edit, and create files
- Run terminal commands
- Debug and fix issues
- Install packages and configure environments
- Build, test, and deploy applications

WORKFLOW:
1. ANALYZE the project structure
2. PLAN your approach - break the task into steps
3. EXECUTE each step - write code, run commands, make changes
4. VERIFY your work - test, check for errors, iterate
5. COMMUNICATE - tell the user what you're doing and thinking

You have a context window of ~4096 tokens. If the project is larger, you'll process it in parts.
"""


class AIAgent:
    """Fully synchronous AI Agent. No asyncio."""

    def __init__(self, groq_service, workspace_base: str = "/tmp/yubilab_workspaces",
                 max_context_tokens: int = 4096, execution_timeout: int = 30):
        self.groq_service = groq_service
        self.workspace_base = workspace_base
        self.context_manager = ContextManager(max_context_tokens=max_context_tokens)
        self.planner = AgentPlanner(groq_service)
        self.sessions: Dict[str, AgentSession] = {}
        os.makedirs(workspace_base, exist_ok=True)

    def create_session(self, user_id: str, task: str, project_path: str = None) -> AgentSession:
        session_id = f"agent_{uuid.uuid4().hex[:12]}"
        workspace_path = os.path.join(self.workspace_base, session_id)
        os.makedirs(workspace_path, exist_ok=True)

        if project_path and os.path.exists(project_path):
            for item in os.listdir(project_path):
                src = os.path.join(project_path, item)
                dst = os.path.join(workspace_path, item)
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)

        session = AgentSession(
            session_id=session_id,
            user_id=user_id,
            workspace_path=workspace_path,
            task=task,
        )
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[AgentSession]:
        return self.sessions.get(session_id)

    def stream_event(self, session: AgentSession, event_type: str, data: Dict) -> Dict:
        event = {
            "type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "session_id": session.session_id,
            **data,
        }
        session.activity_log.append(event)
        session.updated_at = datetime.utcnow().isoformat()
        return event

    def analyze_project(self, session: AgentSession) -> ProjectContext:
        session.state = AgentState.ANALYZING
        self.stream_event(session, "agent_thinking", {
            "thinking": "Analyzing project structure and codebase...",
            "state": "analyzing",
        })
        project_context = self.context_manager.create_project_context(
            session.workspace_path,
            project_name=os.path.basename(session.workspace_path),
        )
        session.project_context = project_context
        session.total_parts = len(project_context.parts)
        self.stream_event(session, "project_analyzed", {
            "message": f"Project analyzed: {project_context.total_files} files, "
                       f"~{project_context.total_tokens_estimate} tokens, "
                       f"{len(project_context.parts)} parts",
            "total_files": project_context.total_files,
            "total_tokens": project_context.total_tokens_estimate,
            "total_parts": len(project_context.parts),
            "file_tree": project_context.file_tree,
        })
        return project_context

    def run_agent_loop(self, session: AgentSession, user_message: str = None) -> Dict:
        """Main autonomous agent loop. FULLY SYNCHRONOUS - no asyncio."""
        if user_message:
            session.conversation_history.append({"role": "user", "content": user_message})

        # Step 1: Analyze project
        if not session.project_context:
            self.analyze_project(session)

        # Step 2: Plan
        session.state = AgentState.PLANNING
        self.stream_event(session, "agent_thinking", {
            "thinking": "Creating execution plan...",
            "state": "planning",
        })

        context_str = ""
        if session.project_context and session.project_context.parts:
            part = self.context_manager.get_next_part(session.project_context)
            if part:
                context_str = self.context_manager.get_context_for_prompt(session.project_context, part)
                session.current_part = part.index

        plan = self.planner.create_plan(
            task=session.task,
            project_context=context_str,
            conversation_history=session.conversation_history,
        )
        session.plan = plan

        self.stream_event(session, "plan_created", {
            "message": f"Plan created: {len(plan.steps)} steps",
            "plan": plan.to_dict(),
        })

        # Step 3: Execute each step
        session.state = AgentState.EXECUTING
        executor = AgentExecutor(session.workspace_path)

        for step in plan.steps:
            self.stream_event(session, "step_started", {
                "message": f"Executing: {step.description}",
                "step": step.to_dict(),
            })

            action_data = self.planner.execute_step(
                step=step,
                context=context_str,
                conversation_history=session.conversation_history,
            )

            action = action_data.get("action", {})
            thinking = action_data.get("thinking", "")

            if thinking:
                self.stream_event(session, "agent_thinking", {
                    "thinking": thinking,
                    "step_id": step.id,
                })

            if action.get("type") not in ("done", "communicate"):
                result = executor.execute_step(action)
                self.stream_event(session, "action_executed", {
                    "step_id": step.id,
                    "action_type": action.get("type"),
                    "success": result.success,
                    "output": result.output[:2000],
                    "error": result.error[:500] if result.error else None,
                })
                step.result = result.output if result.success else f"Error: {result.error}"

                if not result.success and action.get("type") == "run_command":
                    session.conversation_history.append({
                        "role": "assistant",
                        "content": f"Command failed: {action.get('command')}\nError: {result.error}",
                    })
            else:
                message = action.get("message", "")
                if message:
                    self.stream_event(session, "agent_message", {"message": message, "step_id": step.id})
                step.result = message or "Step completed"
                step.status = StepStatus.COMPLETED

            session.conversation_history.append({"role": "assistant", "content": step.result or step.description})
            plan.advance()

        # Step 4: Verify
        session.state = AgentState.VERIFYING
        verification = executor.verify_execution(plan)

        self.stream_event(session, "verification_complete", {
            "message": f"Execution complete. {verification['completed']}/{verification['total_steps']} steps completed.",
            "verification": verification,
        })

        # Mark part as processed
        if session.project_context:
            self.context_manager.mark_part_processed(session.project_context, session.current_part)
            next_part = self.context_manager.get_next_part(session.project_context)
            if next_part and not session.project_context.analysis_complete:
                self.stream_event(session, "more_parts", {
                    "message": f"Part {session.current_part + 1} done. Continuing...",
                    "progress": self.context_manager.get_progress(session.project_context),
                })
                session.state = AgentState.EXECUTING
                return self.run_agent_loop(session)

        session.state = AgentState.COMPLETED
        self.stream_event(session, "task_completed", {
            "message": "Task completed!",
            "verification": verification,
        })

        return {
            "status": "completed",
            "session_id": session.session_id,
            "verification": verification,
        }

    def chat(self, session_id: str, message: str) -> Dict:
        """Handle a chat message. FULLY SYNCHRONOUS."""
        session = self.get_session(session_id)
        if not session:
            return {"error": "Session not found"}

        session.conversation_history.append({"role": "user", "content": message})
        self.stream_event(session, "user_message", {"message": message})

        result = self.run_agent_loop(session, user_message=message)
        return result

    def process_uploaded_project(self, session_id: str, archive_path: str) -> Dict:
        session = self.get_session(session_id)
        if not session:
            return {"error": "Session not found"}

        from services.archive import ArchiveExtractor
        extractor = ArchiveExtractor()
        extraction_result = extractor.extract(archive_path, session.workspace_path)
        if not extraction_result["success"]:
            return {"error": f"Failed to extract archive: {extraction_result['error']}"}

        self.stream_event(session, "project_uploaded", {
            "message": f"Project extracted: {extraction_result['file_count']} files",
            "file_count": extraction_result["file_count"],
        })
        self.analyze_project(session)
        return {
            "status": "project_loaded",
            "session_id": session_id,
            "project_context": session.project_context.to_dict() if session.project_context else None,
        }

    def get_activity_log(self, session_id: str) -> List[Dict]:
        session = self.get_session(session_id)
        if not session:
            return []
        return session.activity_log

    def list_sessions(self, user_id: str = None) -> List[Dict]:
        sessions = self.sessions.values()
        if user_id:
            sessions = [s for s in sessions if s.user_id == user_id]
        return [s.to_dict() for s in sessions]

    def delete_session(self, session_id: str) -> bool:
        session = self.get_session(session_id)
        if not session:
            return False
        if os.path.exists(session.workspace_path):
            shutil.rmtree(session.workspace_path, ignore_errors=True)
        del self.sessions[session_id]
        return True
