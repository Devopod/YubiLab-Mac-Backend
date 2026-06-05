"""
AI Agent Executor - Executes the planned steps and manages the agent loop.
Handles file operations, command execution, and result verification.
"""

import os
import subprocess
import time
import shutil
import re
import json
from pathlib import Path
from typing import Dict, Optional, List, Any
from datetime import datetime

from .planner import AgentStep, StepType, StepStatus, AgentPlan


class ExecutionResult:
    """Result of executing a step."""

    def __init__(self, success: bool, output: str = "", error: str = "", data: Dict = None):
        self.success = success
        self.output = output
        self.error = error
        self.data = data or {}
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "output": self.output[:5000],  # Limit output size
            "error": self.error[:2000] if self.error else "",
            "data": self.data,
            "timestamp": self.timestamp,
        }


class AgentExecutor:
    """
    Executes agent steps in the workspace.
    Handles file operations, command execution, and verification.
    """

    def __init__(self, workspace_root: str, execution_timeout: int = 30):
        self.workspace_root = Path(workspace_root)
        self.execution_timeout = execution_timeout
        self.execution_log: List[Dict] = []

    def resolve_path(self, file_path: str) -> Path:
        """Resolve a file path relative to the workspace root."""
        # Prevent path traversal attacks
        resolved = (self.workspace_root / file_path).resolve()
        try:
            resolved.relative_to(self.workspace_root.resolve())
        except ValueError:
            raise ValueError(f"Path traversal detected: {file_path}")
        return resolved

    def execute_step(self, action: Dict) -> ExecutionResult:
        """Execute an action from the AI agent."""
        action_type = action.get("type", "done")
        start_time = time.time()

        try:
            if action_type == "write_file" or action_type == "create_file":
                result = self._write_file(action)
            elif action_type == "edit_file":
                result = self._edit_file(action)
            elif action_type == "run_command":
                result = self._run_command(action)
            elif action_type == "delete_file":
                result = self._delete_file(action)
            elif action_type == "search":
                result = self._search(action)
            elif action_type == "read_file":
                result = self._read_file(action)
            elif action_type == "done" or action_type == "communicate":
                result = ExecutionResult(
                    success=True,
                    output=action.get("message", "Step completed"),
                    data=action,
                )
            else:
                result = ExecutionResult(
                    success=False,
                    error=f"Unknown action type: {action_type}",
                )
        except Exception as e:
            result = ExecutionResult(success=False, error=str(e))

        duration_ms = int((time.time() - start_time) * 1000)
        self.execution_log.append({
            "action_type": action_type,
            "duration_ms": duration_ms,
            "success": result.success,
            "timestamp": datetime.utcnow().isoformat(),
        })

        return result

    def _write_file(self, action: Dict) -> ExecutionResult:
        """Write content to a file, creating directories as needed."""
        file_path = action.get("file_path", "")
        content = action.get("content", "")

        if not file_path:
            return ExecutionResult(success=False, error="No file_path specified")

        resolved = self.resolve_path(file_path)
        resolved.parent.mkdir(parents=True, exist_ok=True)

        with open(resolved, 'w', encoding='utf-8') as f:
            f.write(content)

        return ExecutionResult(
            success=True,
            output=f"File written: {file_path} ({len(content)} bytes)",
            data={"file_path": file_path, "size": len(content)},
        )

    def _edit_file(self, action: Dict) -> ExecutionResult:
        """Edit a file by replacing old content with new content."""
        file_path = action.get("file_path", "")
        old_content = action.get("old_content", "")
        new_content = action.get("new_content", "")

        if not file_path:
            return ExecutionResult(success=False, error="No file_path specified")

        resolved = self.resolve_path(file_path)

        if not resolved.exists():
            return ExecutionResult(success=False, error=f"File not found: {file_path}")

        with open(resolved, 'r', encoding='utf-8') as f:
            content = f.read()

        if old_content not in content:
            # Try fuzzy matching - strip whitespace
            old_stripped = old_content.strip()
            content_lines = content.split('\n')

            found = False
            for i, line in enumerate(content_lines):
                if old_stripped in line or line.strip() == old_stripped:
                    # Found approximate match
                    content = content.replace(line, new_content.strip() if i == 0 else new_content)
                    found = True
                    break

            if not found:
                return ExecutionResult(
                    success=False,
                    error=f"old_content not found in file: {file_path}",
                    data={"file_path": file_path, "old_content_preview": old_content[:200]},
                )
        else:
            content = content.replace(old_content, new_content, 1)

        with open(resolved, 'w', encoding='utf-8') as f:
            f.write(content)

        return ExecutionResult(
            success=True,
            output=f"File edited: {file_path}",
            data={"file_path": file_path},
        )

    def _run_command(self, action: Dict) -> ExecutionResult:
        """Execute a shell command in the workspace."""
        command = action.get("command", "")

        if not command:
            return ExecutionResult(success=False, error="No command specified")

        # Security: Block dangerous commands
        dangerous_patterns = [
            r'rm\s+-rf\s+/', r'mkfs', r'dd\s+if=', r':\(\)\{.*;\}',
            r'wget.*\|\s*sh', r'curl.*\|\s*sh', r'chmod\s+777',
        ]
        for pattern in dangerous_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return ExecutionResult(
                    success=False,
                    error=f"Blocked potentially dangerous command: {command[:100]}",
                )

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=self.execution_timeout,
                env={**os.environ, "TERM": "dumb"},
            )

            output = result.stdout
            error = result.stderr

            # Truncate large outputs
            max_len = 4096
            if len(output) > max_len:
                output = output[:max_len] + f"\n... (truncated, {len(output)} total bytes)"
            if len(error) > max_len:
                error = error[:max_len] + f"\n... (truncated, {len(error)} total bytes)"

            return ExecutionResult(
                success=result.returncode == 0,
                output=output,
                error=error if result.returncode != 0 else "",
                data={
                    "command": command,
                    "return_code": result.returncode,
                },
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False,
                error=f"Command timed out after {self.execution_timeout}s",
                data={"command": command},
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                error=str(e),
                data={"command": command},
            )

    def _delete_file(self, action: Dict) -> ExecutionResult:
        """Delete a file from the workspace."""
        file_path = action.get("file_path", "")

        if not file_path:
            return ExecutionResult(success=False, error="No file_path specified")

        resolved = self.resolve_path(file_path)

        if not resolved.exists():
            return ExecutionResult(success=False, error=f"File not found: {file_path}")

        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink()

        return ExecutionResult(
            success=True,
            output=f"Deleted: {file_path}",
            data={"file_path": file_path},
        )

    def _search(self, action: Dict) -> ExecutionResult:
        """Search for text patterns in the workspace."""
        query = action.get("search_query", "")
        file_pattern = action.get("file_pattern", "*")

        if not query:
            return ExecutionResult(success=False, error="No search_query specified")

        results = []
        for root, dirs, files in os.walk(self.workspace_root):
            # Skip hidden and common build directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in {
                'node_modules', '__pycache__', 'venv', '.venv', 'dist', 'build', '.git'
            }]

            for file in files:
                file_path = Path(root) / file
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for line_num, line in enumerate(f, 1):
                            if query.lower() in line.lower():
                                rel_path = file_path.relative_to(self.workspace_root)
                                results.append({
                                    "file": str(rel_path),
                                    "line": line_num,
                                    "content": line.strip()[:200],
                                })
                                if len(results) >= 50:
                                    break
                except (IOError, OSError):
                    continue
                if len(results) >= 50:
                    break

        return ExecutionResult(
            success=True,
            output=f"Found {len(results)} matches for '{query}'",
            data={"query": query, "results": results},
        )

    def _read_file(self, action: Dict) -> ExecutionResult:
        """Read a file from the workspace."""
        file_path = action.get("file_path", "")

        if not file_path:
            return ExecutionResult(success=False, error="No file_path specified")

        resolved = self.resolve_path(file_path)

        if not resolved.exists():
            return ExecutionResult(success=False, error=f"File not found: {file_path}")

        if resolved.is_dir():
            # List directory contents
            entries = sorted(resolved.iterdir(), key=lambda x: (not x.is_dir(), x.name))
            content = "\n".join(
                f"{'📁' if e.is_dir() else '📄'} {e.name}"
                for e in entries
            )
            return ExecutionResult(
                success=True,
                output=content,
                data={"file_path": file_path, "is_directory": True},
            )

        try:
            with open(resolved, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()

            max_len = 4096
            if len(content) > max_len:
                content = content[:max_len] + "\n... (file truncated, use specific line ranges)"

            return ExecutionResult(
                success=True,
                output=content,
                data={"file_path": file_path, "size": len(content)},
            )
        except Exception as e:
            return ExecutionResult(success=False, error=str(e))

    def verify_execution(self, plan: AgentPlan) -> Dict:
        """Verify the results of execution by checking key indicators."""
        total_steps = len(plan.steps)
        completed = sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED)
        failed = sum(1 for s in plan.steps if s.status == StepStatus.FAILED)

        return {
            "total_steps": total_steps,
            "completed": completed,
            "failed": failed,
            "success_rate": (completed / total_steps * 100) if total_steps > 0 else 0,
            "execution_log_entries": len(self.execution_log),
            "workspace_exists": self.workspace_root.exists(),
        }
