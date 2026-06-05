"""
AI Agent Planner - FULLY SYNCHRONOUS.
Creates step-by-step execution plans for tasks.
Like Devin, the agent thinks about what to do before doing it.
NO asyncio - compatible with eventlet + gunicorn on macOS.
"""

import json
import re
import uuid
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from enum import Enum


class StepType(Enum):
    ANALYZE = "analyze"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    EDIT_FILE = "edit_file"
    RUN_COMMAND = "run_command"
    INSTALL_PACKAGE = "install_package"
    CREATE_FILE = "create_file"
    DELETE_FILE = "delete_file"
    SEARCH = "search"
    VERIFY = "verify"
    THINK = "think"
    COMMUNICATE = "communicate"


class StepStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class AgentStep:
    id: str
    step_type: StepType
    description: str
    details: Dict = field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    result: Optional[str] = None
    thinking: Optional[str] = None
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "step_type": self.step_type.value,
            "description": self.description,
            "details": self.details,
            "status": self.status.value,
            "result": self.result,
            "thinking": self.thinking,
            "duration_ms": self.duration_ms,
        }


@dataclass
class AgentPlan:
    task_id: str
    task_description: str
    steps: List[AgentStep] = field(default_factory=list)
    current_step_index: int = 0
    overall_status: str = "planning"
    estimated_complexity: str = "medium"

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_description": self.task_description,
            "steps": [s.to_dict() for s in self.steps],
            "current_step_index": self.current_step_index,
            "overall_status": self.overall_status,
            "estimated_complexity": self.estimated_complexity,
            "total_steps": len(self.steps),
            "completed_steps": sum(1 for s in self.steps if s.status == StepStatus.COMPLETED),
        }

    def get_current_step(self) -> Optional[AgentStep]:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def advance(self) -> Optional[AgentStep]:
        self.current_step_index += 1
        return self.get_current_step()


PLANNER_SYSTEM_PROMPT = """You are YubiLab AI Agent - an autonomous software engineer like Devin, Replit Agent, and z.ai.

Your job is to CREATE A DETAILED EXECUTION PLAN for the user's task. Think step by step about what needs to be done.

You MUST respond with valid JSON in this exact format:
{
    "thinking": "Your internal reasoning about the task",
    "complexity": "simple|medium|complex",
    "steps": [
        {
            "id": "step_1",
            "type": "analyze|read_file|write_file|edit_file|run_command|install_package|create_file|delete_file|search|verify|think|communicate",
            "description": "What this step does",
            "details": {
                "file_path": "path/to/file (if applicable)",
                "command": "command to run (if applicable)",
                "content": "content to write (if applicable)",
                "search_query": "what to search for (if applicable)"
            }
        }
    ]
}

IMPORTANT RULES:
1. Always start with an ANALYZE step to understand the project structure
2. Break complex tasks into small, atomic steps
3. Include VERIFY steps to check your work
4. Include COMMUNICATE steps to tell the user what you're doing
5. For bug fixes: analyze → find root cause → fix → verify
6. For features: analyze → plan → implement → test → verify
7. For new projects: create files → install packages → test → verify
8. Each step should be independently executable
9. Always end with a VERIFY step
10. Write production-quality, clean, well-commented code
"""

EXECUTOR_SYSTEM_PROMPT = """You are YubiLab AI Agent - an autonomous software engineer executing a specific step.

You MUST respond with valid JSON in this exact format:
{
    "thinking": "Your reasoning about this step",
    "action": {
        "type": "write_file|edit_file|run_command|create_file|delete_file|search|communicate|done",
        "file_path": "path/to/file (for file operations)",
        "content": "full file content (for write_file/create_file)",
        "old_content": "text to find (for edit_file)",
        "new_content": "replacement text (for edit_file)",
        "command": "command to execute (for run_command)",
        "search_query": "what to search (for search)",
        "message": "message for user (for communicate)"
    },
    "result_summary": "Brief summary of what was done",
    "needs_verification": true/false,
    "next_step_suggestion": "What to do next, or null"
}

IMPORTANT RULES:
1. For write_file/create_file: Provide the COMPLETE file content
2. For edit_file: Provide old_content to find and new_content to replace it with
3. For run_command: Provide the exact shell command
4. For communicate: Tell the user what you're doing/thinking
5. For done: This step is complete, move to next
6. Write production-quality, clean, well-commented code
7. Follow existing code patterns in the project
"""


class AgentPlanner:
    """Creates and manages execution plans for the AI agent. Fully synchronous."""

    def __init__(self, groq_service):
        self.groq_service = groq_service

    def create_plan(self, task: str, project_context: str = "", conversation_history: List[Dict] = None) -> AgentPlan:
        """Create an execution plan for the given task. SYNCHRONOUS."""
        task_id = f"task_{uuid.uuid4().hex[:8]}"

        user_message = f"Task: {task}"
        if project_context:
            user_message = f"{project_context}\n\n---\n\n{user_message}"

        messages = [{"role": "system", "content": PLANNER_SYSTEM_PROMPT}]
        if conversation_history:
            messages.extend(conversation_history[-6:])
        messages.append({"role": "user", "content": user_message})

        # Synchronous Groq call
        response = self.groq_service.chat_completion(messages=messages, max_tokens=4096, temperature=0.3)

        plan_data = self._parse_json_response(response)
        if not plan_data:
            plan_data = {
                "thinking": "Creating a basic plan",
                "complexity": "medium",
                "steps": [
                    {"id": "step_1", "type": "analyze", "description": f"Analyze: {task}", "details": {}},
                    {"id": "step_2", "type": "communicate", "description": "Discuss approach", "details": {"message": f"I'll work on: {task}"}},
                    {"id": "step_3", "type": "think", "description": "Plan implementation", "details": {}},
                ]
            }

        steps = []
        for step_data in plan_data.get("steps", []):
            step_type_str = step_data.get("type", "think")
            try:
                step_type = StepType(step_type_str)
            except ValueError:
                step_type = StepType.THINK
            steps.append(AgentStep(
                id=step_data.get("id", f"step_{len(steps) + 1}"),
                step_type=step_type,
                description=step_data.get("description", ""),
                details=step_data.get("details", {}),
            ))

        return AgentPlan(
            task_id=task_id,
            task_description=task,
            steps=steps,
            estimated_complexity=plan_data.get("complexity", "medium"),
        )

    def execute_step(self, step: AgentStep, context: str = "", conversation_history: List[Dict] = None) -> Dict:
        """Execute a single step using the AI. SYNCHRONOUS."""
        step.status = StepStatus.IN_PROGRESS

        user_message = f"""Step to Execute:
Type: {step.step_type.value}
Description: {step.description}
Details: {json.dumps(step.details, indent=2) if step.details else 'None'}
"""
        if context:
            user_message = f"{context}\n\n---\n\n{user_message}"

        messages = [{"role": "system", "content": EXECUTOR_SYSTEM_PROMPT}]
        if conversation_history:
            messages.extend(conversation_history[-8:])
        messages.append({"role": "user", "content": user_message})

        # Synchronous Groq call
        response = self.groq_service.chat_completion(messages=messages, max_tokens=4096, temperature=0.2)

        action_data = self._parse_json_response(response)
        if not action_data:
            action_data = {
                "thinking": "Processing step",
                "action": {"type": "done"},
                "result_summary": "Step processed",
                "needs_verification": False,
            }

        step.thinking = action_data.get("thinking", "")
        step.result = action_data.get("result_summary", "")
        step.status = StepStatus.COMPLETED

        return action_data

    def _parse_json_response(self, response: str) -> Optional[Dict]:
        """Parse JSON from AI response."""
        if not response:
            return None
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        json_pattern = r'```(?:json)?\s*\n?(.*?)\n?```'
        matches = re.findall(json_pattern, response, re.DOTALL)
        for match in matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue
        brace_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
        matches = re.findall(brace_pattern, response, re.DOTALL)
        for match in matches:
            try:
                return json.loads(match)
            except json.JSONDecodeError:
                continue
        return None
