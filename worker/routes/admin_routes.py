"""Admin panel routes — manage deposits, users, settings."""
import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from worker.db.database import db
from worker.config import config

router = APIRouter(prefix="/admin", tags=["admin"])

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "uploads", "bkash_screenshots")


class ApproveDeposit(BaseModel):
    deposit_id: int
    admin_note: str = ""


class RejectDeposit(BaseModel):
    deposit_id: int
    admin_note: str = "Rejected by admin"


class ChangeTier(BaseModel):
    user_id: int
    tier: int


class BanUser(BaseModel):
    user_id: int
    ban: bool


async def _is_admin(user_id: int):
    user = await db.fetch_one("SELECT * FROM users WHERE id = ?", [user_id])
    if not user or not user.get('is_admin'):
        raise HTTPException(403, "Admin access required")
    return user


@router.get("/dashboard")
async def admin_dashboard(user_id: int):
    await _is_admin(user_id)

    total_users = await db.fetch_one("SELECT COUNT(*) as count FROM users")
    pro_users = await db.fetch_one("SELECT COUNT(*) as count FROM users WHERE tier = 2")
    ent_users = await db.fetch_one("SELECT COUNT(*) as count FROM users WHERE tier = 3")
    pending = await db.fetch_one("SELECT COUNT(*) as count FROM deposits WHERE status = 'pending'")
    revenue = await db.fetch_one("SELECT COALESCE(SUM(amount), 0) as total FROM deposits WHERE status = 'approved'")
    today_runs = await db.fetch_one("SELECT COUNT(*) as count FROM agent_runs WHERE date(created_at) = date('now')")

    return {
        "total_users": total_users['count'],
        "pro_users": pro_users['count'],
        "enterprise_users": ent_users['count'],
        "pending_deposits": pending['count'],
        "total_revenue_bdt": revenue['total'],
        "agent_runs_today": today_runs['count']
    }


@router.get("/deposits")
async def list_deposits(user_id: int, status: str = "pending"):
    await _is_admin(user_id)

    if status == "all":
        deposits = await db.fetch_all(
            "SELECT d.*, u.username, u.email FROM deposits d JOIN users u ON d.user_id = u.id ORDER BY d.created_at DESC LIMIT 100"
        )
    else:
        deposits = await db.fetch_all(
            "SELECT d.*, u.username, u.email FROM deposits d JOIN users u ON d.user_id = u.id WHERE d.status = ? ORDER BY d.created_at DESC LIMIT 100",
            [status]
        )

    return {"deposits": deposits}


@router.get("/deposit/screenshot/{filename}")
async def get_screenshot(filename: str, user_id: int):
    await _is_admin(user_id)
    filepath = os.path.join(SCREENSHOT_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(404, "Screenshot not found")
    return FileResponse(filepath)


@router.post("/deposits/approve")
async def approve_deposit(data: ApproveDeposit, user_id: int):
    await _is_admin(user_id)

    deposit = await db.fetch_one("SELECT * FROM deposits WHERE id = ?", [data.deposit_id])
    if not deposit:
        raise HTTPException(404, "Deposit not found")
    if deposit['status'] != 'pending':
        raise HTTPException(400, f"Deposit already {deposit['status']}")

    await db.execute("""
        UPDATE deposits SET status = 'approved', admin_note = ?, reviewed_by = ?, reviewed_at = datetime('now')
        WHERE id = ?
    """, (data.admin_note, user_id, data.deposit_id))

    await db.execute(
        "UPDATE users SET tier = ? WHERE id = ?",
        (deposit['target_tier'], deposit['user_id'])
    )

    return {
        "message": f"Deposit approved. User upgraded to tier {deposit['target_tier']}",
        "deposit_id": data.deposit_id,
        "user_upgraded_to": deposit['target_tier']
    }


@router.post("/deposits/reject")
async def reject_deposit(data: RejectDeposit, user_id: int):
    await _is_admin(user_id)

    deposit = await db.fetch_one("SELECT * FROM deposits WHERE id = ?", [data.deposit_id])
    if not deposit:
        raise HTTPException(404, "Deposit not found")
    if deposit['status'] != 'pending':
        raise HTTPException(400, f"Deposit already {deposit['status']}")

    await db.execute("""
        UPDATE deposits SET status = 'rejected', admin_note = ?, reviewed_by = ?, reviewed_at = datetime('now')
        WHERE id = ?
    """, (data.admin_note, user_id, data.deposit_id))

    return {"message": "Deposit rejected", "deposit_id": data.deposit_id}


@router.get("/users")
async def list_users(user_id: int, search: str = ""):
    await _is_admin(user_id)

    if search:
        users = await db.fetch_all(
            "SELECT id, username, email, tier, is_banned, ai_prompts_used_today, created_at FROM users WHERE username LIKE ? OR email LIKE ? ORDER BY id DESC LIMIT 100",
            [f"%{search}%", f"%{search}%"]
        )
    else:
        users = await db.fetch_all(
            "SELECT id, username, email, tier, is_banned, ai_prompts_used_today, created_at FROM users ORDER BY id DESC LIMIT 100"
        )

    return {"users": users}


@router.post("/users/tier")
async def change_tier(data: ChangeTier, user_id: int):
    await _is_admin(user_id)
    if data.tier not in [1, 2, 3]:
        raise HTTPException(400, "Tier must be 1, 2, or 3")
    await db.execute("UPDATE users SET tier = ? WHERE id = ?", (data.tier, data.user_id))
    return {"message": f"User {data.user_id} tier changed to {data.tier}"}


@router.post("/users/ban")
async def ban_user(data: BanUser, user_id: int):
    await _is_admin(user_id)
    await db.execute("UPDATE users SET is_banned = ? WHERE id = ?", (1 if data.ban else 0, data.user_id))
    action = "banned" if data.ban else "unbanned"
    return {"message": f"User {data.user_id} {action}"}


@router.get("/solve-logs")
async def admin_solve_logs(user_id: int, workspace_id: str = "", limit: int = 100):
    await _is_admin(user_id)

    if workspace_id:
        logs = await db.fetch_all(
            "SELECT * FROM solve_log WHERE workspace_id = ? ORDER BY timestamp DESC LIMIT ?",
            [workspace_id, limit]
        )
    else:
        logs = await db.fetch_all(
            "SELECT * FROM solve_log ORDER BY timestamp DESC LIMIT ?",
            [limit]
        )
    return {"logs": logs}


@router.get("/settings")
async def get_settings(user_id: int):
    await _is_admin(user_id)
    return {
        "bkash_number": config.BKASH_NUMBER,
        "pro_amount": config.BKASH_AMOUNT,
        "enterprise_amount": config.BKASH_ENTERPRISE_AMOUNT,
        "free_ai_prompts_daily": config.FREE_AI_PROMPTS_DAILY,
        "free_apk_builds_daily": config.FREE_APK_BUILDS_DAILY,
        "groq_model": config.GROQ_MODEL,
        "groq_key_1_set": bool(config.GROQ_API_KEY),
        "groq_key_2_set": bool(config.GROQ_API_KEY_2),
    }
