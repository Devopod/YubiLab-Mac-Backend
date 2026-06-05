"""bKash deposit routes — manual deposit with TrxID + screenshot."""
import os
import uuid
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from worker.db.database import db
from worker.config import config

router = APIRouter(prefix="/deposits", tags=["deposits"])

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "uploads", "bkash_screenshots")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/submit")
async def submit_deposit(
    trx_id: str = Form(...),
    amount: int = Form(...),
    target_tier: int = Form(2),
    screenshot: UploadFile = File(...),
    user_id: int = Form(...)
):
    """Submit bKash deposit for admin approval. Both trx_id and screenshot are MANDATORY."""
    if not trx_id or len(trx_id.strip()) < 5:
        raise HTTPException(400, "Valid TrxID is required (min 5 chars)")

    if not screenshot.filename:
        raise HTTPException(400, "Screenshot is required")

    allowed_types = ['image/png', 'image/jpeg', 'image/jpg', 'image/webp']
    if screenshot.content_type not in allowed_types:
        raise HTTPException(400, "Screenshot must be PNG, JPG, or WEBP")

    valid_amounts = [config.BKASH_AMOUNT, config.BKASH_ENTERPRISE_AMOUNT]
    if amount not in valid_amounts:
        raise HTTPException(400, f"Amount must be {config.BKASH_AMOUNT} (Pro) or {config.BKASH_ENTERPRISE_AMOUNT} (Enterprise)")

    if target_tier not in [2, 3]:
        raise HTTPException(400, "Target tier must be 2 (Pro) or 3 (Enterprise)")

    expected = config.BKASH_AMOUNT if target_tier == 2 else config.BKASH_ENTERPRISE_AMOUNT
    if amount != expected:
        raise HTTPException(400, f"Amount for tier {target_tier} must be {expected}")

    existing = await db.fetch_one(
        "SELECT id FROM deposits WHERE trx_id = ? AND status IN ('pending', 'approved')",
        [trx_id.strip()]
    )
    if existing:
        raise HTTPException(400, "This TrxID has already been submitted")

    ext = screenshot.filename.rsplit('.', 1)[-1] if '.' in screenshot.filename else 'png'
    file_id = str(uuid.uuid4())
    filename = f"{file_id}.{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as f:
        content = await screenshot.read()
        f.write(content)

    deposit_id = await db.execute("""
        INSERT INTO deposits (user_id, amount, trx_id, screenshot_path, target_tier, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
    """, (user_id, amount, trx_id.strip(), filename, target_tier))

    return {
        "message": "Deposit submitted successfully. Admin will review shortly.",
        "status": "pending",
        "deposit_id": deposit_id.lastrowid if hasattr(deposit_id, 'lastrowid') else None
    }


@router.get("/my")
async def my_deposits(user_id: int):
    """Get user's deposit history"""
    deposits = await db.fetch_all(
        "SELECT * FROM deposits WHERE user_id = ? ORDER BY created_at DESC",
        [user_id]
    )
    return {"deposits": deposits}


@router.get("/bkash-info")
async def bkash_info():
    """Get bKash payment information"""
    return {
        "number": config.BKASH_NUMBER,
        "pro_amount": config.BKASH_AMOUNT,
        "enterprise_amount": config.BKASH_ENTERPRISE_AMOUNT,
        "instructions": [
            "1. Open bKash app",
            f"2. Send Money to {config.BKASH_NUMBER}",
            f"3. Amount: {config.BKASH_AMOUNT} for Pro, {config.BKASH_ENTERPRISE_AMOUNT} for Enterprise",
            "4. Save the TrxID after payment",
            "5. Come back here and submit TrxID + screenshot",
        ]
    }
