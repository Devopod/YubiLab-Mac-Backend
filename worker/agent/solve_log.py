import json
import uuid
from datetime import datetime
from worker.db.database import db


class SolveLog:
    """
    Anti-loop memory. Every attempt (success/failure) is recorded.
    Before each step, agent checks solve log to avoid repeating
    failed approaches. Same approach failing 3x = loop detected.
    """

    async def record(self, workspace_id, problem_type, problem,
                     approach, tool_calls=None, result_summary="",
                     error=None, success=False, agent_id=None,
                     parent_agent_id=None, files_modified=None,
                     commands_run=None, duration_ms=0):
        entry_id = str(uuid.uuid4())

        await db.execute("""
            INSERT INTO solve_log
            (id, workspace_id, problem_type, problem_description,
             approach, tool_calls, result_summary, error, success,
             agent_id, parent_agent_id, files_modified, commands_run,
             duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry_id, workspace_id, problem_type, problem,
            approach,
            json.dumps(tool_calls or []),
            result_summary[:500],
            error[:500] if error else None,
            1 if success else 0,
            agent_id, parent_agent_id,
            json.dumps(files_modified or []),
            json.dumps(commands_run or []),
            duration_ms
        ))

        return entry_id

    async def search(self, workspace_id, problem_type=None,
                     success_only=False, limit=10):
        query = "SELECT * FROM solve_log WHERE workspace_id = ?"
        params = [workspace_id]

        if problem_type:
            query += " AND problem_type = ?"
            params.append(problem_type)
        if success_only:
            query += " AND success = 1"

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        return await db.fetch_all(query, params)

    async def check_loop(self, workspace_id, current_approach,
                         threshold=3):
        """Check if agent is looping on same failed approach."""
        rows = await db.fetch_all("""
            SELECT approach, error, success
            FROM solve_log
            WHERE workspace_id = ?
            AND timestamp > datetime('now', '-10 minutes')
            AND success = 0
            ORDER BY timestamp DESC LIMIT 20
        """, [workspace_id])

        same = [r for r in rows if r['approach'] == current_approach]

        if len(same) >= threshold:
            winners = await self.search(
                workspace_id, success_only=True, limit=5
            )
            return {
                "is_looping": True,
                "loop_count": len(same),
                "message": f"Loop: '{current_approach}' failed {len(same)}x",
                "alternatives": winners
            }

        return {"is_looping": False, "loop_count": len(same)}

    async def get_warnings(self, workspace_id, step_description):
        """Get warnings from past failures for current step type."""
        failures = await db.fetch_all("""
            SELECT approach, error FROM solve_log
            WHERE workspace_id = ?
            AND success = 0
            AND timestamp > datetime('now', '-1 hour')
            ORDER BY timestamp DESC LIMIT 10
        """, [workspace_id])

        if not failures:
            return ""

        warnings = "\n".join(
            f"- Approach '{f['approach']}' failed: {f['error']}"
            for f in failures[:5]
        )
        return f"[PAST FAILURES - DO NOT REPEAT THESE APPROACHES]\n{warnings}"


solve_log = SolveLog()
