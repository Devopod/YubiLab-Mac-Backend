from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from worker.agent.core import AgentOrchestrator
from worker.db.database import db
from worker.config import config

router = APIRouter(prefix="/agent", tags=["agent"])

_active_agents = {}


class AgentRunRequest(BaseModel):
    workspace_id: str
    prompt: str
    mode: str = "autonomous"
    user_id: int = 1


@router.post("/run")
async def run_agent(request: AgentRunRequest):
    """Start an autonomous agent run"""
    user = await db.fetch_one("SELECT * FROM users WHERE id = ?", [request.user_id])
    if not user:
        raise HTTPException(401, "Unauthorized")
    if user.get('is_banned'):
        raise HTTPException(403, "Account banned")

    tier = await db.fetch_one("SELECT * FROM tiers WHERE id = ?", [user['tier']])

    await _reset_daily(user)

    if tier['ai_prompts_daily'] != -1:
        if user['ai_prompts_used_today'] >= tier['ai_prompts_daily']:
            raise HTTPException(
                429,
                f"Daily AI prompt limit reached ({tier['ai_prompts_daily']}). "
                f"Upgrade to Pro for more."
            )

    await db.execute(
        "UPDATE users SET ai_prompts_used_today = ai_prompts_used_today + 1 WHERE id = ?",
        [user['id']]
    )

    orchestrator = AgentOrchestrator(request.workspace_id, user['id'])
    orchestrator.max_sub_agents = tier['max_sub_agents']

    run_id = await orchestrator.start(
        prompt=request.prompt,
        mode=request.mode,
        max_sub_agents=tier['max_sub_agents']
    )

    _active_agents[run_id] = orchestrator

    return {"run_id": run_id, "status": "running"}


@router.get("/status/{run_id}")
async def agent_status(run_id: str):
    row = await db.fetch_one("SELECT * FROM agent_runs WHERE id = ?", [run_id])
    if not row:
        raise HTTPException(404, "Run not found")
    return row


@router.post("/stop/{run_id}")
async def stop_agent(run_id: str):
    if run_id in _active_agents:
        await _active_agents[run_id].stop()
        del _active_agents[run_id]
    return {"status": "stopped", "run_id": run_id}


@router.get("/runs")
async def list_runs(workspace_id: str):
    runs = await db.fetch_all(
        "SELECT * FROM agent_runs WHERE workspace_id = ? ORDER BY created_at DESC LIMIT 50",
        [workspace_id]
    )
    return {"runs": runs}


@router.get("/run/{run_id}/logs")
async def run_logs(run_id: str):
    logs = await db.fetch_all(
        "SELECT * FROM solve_log WHERE agent_id = ? ORDER BY timestamp",
        [run_id]
    )
    return {"logs": logs}


async def _reset_daily(user):
    from datetime import date
    today = date.today().isoformat()
    if user.get('last_reset_date') != today:
        await db.execute("""
            UPDATE users SET
            ai_prompts_used_today = 0,
            apk_builds_used_today = 0,
            terminal_minutes_used = 0,
            last_reset_date = ?
            WHERE id = ?
        """, [today, user['id']])
