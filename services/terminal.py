"""
Terminal Session Service - Manages persistent terminal sessions for the AI agent.
Supports creating, running commands, and streaming output.
"""

import os
import uuid
import subprocess
import select
import fcntl
import time
from typing import Dict, Optional, List
from datetime import datetime


class TerminalSession:
    """A persistent terminal session."""

    def __init__(self, session_id: str, working_dir: str, timeout: int = 30):
        self.session_id = session_id
        self.working_dir = working_dir
        self.timeout = timeout
        self.created_at = datetime.utcnow().isoformat()
        self.last_active = datetime.utcnow().isoformat()
        self.history: List[Dict] = []
        self.process = None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "working_dir": self.working_dir,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "history_count": len(self.history),
        }


class TerminalService:
    """Manages terminal sessions for workspace command execution."""

    def __init__(self, base_path: str = "/tmp/yubilab_workspaces"):
        self.base_path = base_path
        self.sessions: Dict[str, TerminalSession] = {}

    def create_session(self, workspace_id: str, working_dir: str = None) -> Dict:
        """Create a new terminal session."""
        session_id = f"term_{uuid.uuid4().hex[:10]}"

        ws_path = working_dir or os.path.join(self.base_path, workspace_id)
        if not os.path.exists(ws_path):
            os.makedirs(ws_path, exist_ok=True)

        session = TerminalSession(
            session_id=session_id,
            working_dir=ws_path,
        )
        self.sessions[session_id] = session

        return {
            "success": True,
            "session_id": session_id,
            "working_dir": ws_path,
        }

    def execute_command(self, session_id: str, command: str, timeout: int = None) -> Dict:
        """Execute a command in a terminal session."""
        session = self.sessions.get(session_id)
        if not session:
            return {"success": False, "error": "Session not found"}

        timeout = timeout or session.timeout

        # Security: block dangerous commands
        dangerous = ['rm -rf /', 'mkfs', 'dd if=', ':(){:|:&};:', 'wget | sh', 'curl | sh']
        for d in dangerous:
            if d in command:
                return {"success": False, "error": f"Blocked dangerous command pattern"}

        start_time = time.time()

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=session.working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "TERM": "dumb", "FORCE_COLOR": "0"},
            )

            duration = int((time.time() - start_time) * 1000)

            output = result.stdout or ""
            error = result.stderr or ""

            # Truncate large outputs
            max_len = 4096
            if len(output) > max_len:
                output = output[:max_len] + f"\n... (truncated, {len(output)} total bytes)"
            if len(error) > max_len:
                error = error[:max_len] + f"\n... (truncated, {len(error)} total bytes)"

            entry = {
                "command": command,
                "output": output,
                "error": error,
                "return_code": result.returncode,
                "duration_ms": duration,
                "timestamp": datetime.utcnow().isoformat(),
            }

            session.history.append(entry)
            session.last_active = datetime.utcnow().isoformat()

            return {
                "success": result.returncode == 0,
                "output": output,
                "error": error if result.returncode != 0 else "",
                "return_code": result.returncode,
                "duration_ms": duration,
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"Command timed out after {timeout}s",
                "duration_ms": int((time.time() - start_time) * 1000),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_session(self, session_id: str) -> Optional[Dict]:
        """Get session info."""
        session = self.sessions.get(session_id)
        if not session:
            return None
        return session.to_dict()

    def get_history(self, session_id: str) -> Dict:
        """Get command history for a session."""
        session = self.sessions.get(session_id)
        if not session:
            return {"success": False, "error": "Session not found"}

        return {
            "success": True,
            "session_id": session_id,
            "history": session.history[-50:],  # Last 50 commands
        }

    def delete_session(self, session_id: str) -> Dict:
        """Delete a terminal session."""
        if session_id in self.sessions:
            del self.sessions[session_id]
            return {"success": True}
        return {"success": False, "error": "Session not found"}

    def list_sessions(self) -> List[Dict]:
        """List all active terminal sessions."""
        return [s.to_dict() for s in self.sessions.values()]
