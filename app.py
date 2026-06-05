"""
YubiLab Backend Worker v2.1 - FULLY SYNCHRONOUS
No asyncio - compatible with eventlet + gunicorn on macOS.
Fixed: asyncio.new_event_loop() crash, Union undefined, async generator return.
"""

import os
import sys
import json
import uuid
import logging
from datetime import datetime
from functools import wraps

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.groq_service import GroqService
from services.workspace import WorkspaceService
from services.archive import ArchiveExtractor
from services.terminal import TerminalService
from agent.core import AIAgent, AgentSession, AgentState
from utils.security import (
    generate_jwt, verify_jwt, require_auth, require_admin,
    require_api_key, check_rate_limit, rate_limit, hash_password, verify_password
)
from utils.helpers import sanitize_filename, format_file_size, get_project_summary

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
app.config['SECRET_KEY'] = os.getenv("JWT_SECRET", "yubilab-secure-jwt-2024-production")

CORS(app, origins=os.getenv("ALLOWED_ORIGINS", "*").split(","))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet", ping_timeout=60)

groq_service = GroqService()
workspace_service = WorkspaceService()
archive_extractor = ArchiveExtractor()
terminal_service = TerminalService()

ai_agent = AIAgent(
    groq_service=groq_service,
    workspace_base=os.getenv("WORKSPACE_BASE", "/tmp/yubilab_workspaces"),
    max_context_tokens=4096,
    execution_timeout=int(os.getenv("EXECUTION_TIMEOUT", "30")),
)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "yubilab_admin_2024")

users_db = {}
users_db["admin"] = {
    "user_id": "admin",
    "username": ADMIN_USERNAME,
    "password": hash_password(ADMIN_PASSWORD),
    "plan": "admin",
    "created_at": datetime.utcnow().isoformat(),
}

ALLOWED_EXTENSIONS = {
    'zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz',
    'py', 'js', 'ts', 'jsx', 'tsx', 'html', 'css', 'json',
    'md', 'txt', 'yaml', 'yml', 'toml', 'env', 'sh',
}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ============================================================
# AUTH ROUTES
# ============================================================

@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    email = data.get("email", "").strip()
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    if len(username) < 3:
        return jsonify({"error": "Username must be at least 3 characters"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if username in users_db:
        return jsonify({"error": "Username already exists"}), 409
    user_id = f"user_{uuid.uuid4().hex[:8]}"
    users_db[username] = {
        "user_id": user_id, "username": username,
        "password": hash_password(password), "email": email,
        "plan": "free", "created_at": datetime.utcnow().isoformat(),
    }
    token = generate_jwt(user_id, username, "free")
    return jsonify({"success": True, "token": token, "user": {"user_id": user_id, "username": username, "plan": "free"}}), 201


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    user = users_db.get(username)
    if not user or not verify_password(password, user["password"]):
        return jsonify({"error": "Invalid credentials"}), 401
    token = generate_jwt(user["user_id"], user["username"], user["plan"])
    return jsonify({"success": True, "token": token, "user": {"user_id": user["user_id"], "username": user["username"], "plan": user["plan"]}})


@app.route("/api/auth/me", methods=["GET"])
@require_auth
def get_me():
    user = request.user
    db_user = users_db.get(user.get("username"), {})
    return jsonify({"user_id": user.get("user_id"), "username": user.get("username"), "plan": user.get("plan", "free"), "email": db_user.get("email", "")})


# ============================================================
# AI AGENT ROUTES - ALL SYNCHRONOUS (no asyncio)
# ============================================================

@app.route("/api/agent/sessions", methods=["POST"])
@require_auth
def create_agent_session():
    data = request.get_json() or {}
    task = data.get("task", "")
    if not task:
        return jsonify({"error": "Task description required"}), 400
    user = request.user
    session = ai_agent.create_session(user_id=user["user_id"], task=task, project_path=data.get("project_path"))
    return jsonify({"success": True, "session": session.to_dict()}), 201


@app.route("/api/agent/sessions", methods=["GET"])
@require_auth
def list_agent_sessions():
    user = request.user
    return jsonify({"sessions": ai_agent.list_sessions(user_id=user["user_id"])})


@app.route("/api/agent/sessions/<session_id>", methods=["GET"])
@require_auth
def get_agent_session(session_id):
    session = ai_agent.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"session": session.to_dict()})


@app.route("/api/agent/sessions/<session_id>", methods=["DELETE"])
@require_auth
def delete_agent_session(session_id):
    if ai_agent.delete_session(session_id):
        return jsonify({"success": True})
    return jsonify({"error": "Session not found"}), 404


@app.route("/api/agent/sessions/<session_id>/chat", methods=["POST"])
@require_auth
def agent_chat(session_id):
    """Agent chat - FULLY SYNCHRONOUS, no asyncio."""
    data = request.get_json()
    if not data or not data.get("message"):
        return jsonify({"error": "Message required"}), 400

    session = ai_agent.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    try:
        # DIRECT SYNC CALL - no asyncio.new_event_loop()
        result = ai_agent.chat(session_id, data["message"])
        return jsonify(result)
    except Exception as e:
        logger.error(f"Agent chat error: {str(e)}", exc_info=True)
        return jsonify({"error": str(e), "status": "error"}), 500


@app.route("/api/agent/sessions/<session_id>/activity", methods=["GET"])
@require_auth
def agent_activity(session_id):
    return jsonify({"activity": ai_agent.get_activity_log(session_id)})


@app.route("/api/agent/sessions/<session_id>/analyze", methods=["POST"])
@require_auth
def analyze_project(session_id):
    session = ai_agent.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    context = ai_agent.analyze_project(session)
    return jsonify({"success": True, "project_context": context.to_dict()})


# ============================================================
# FILE UPLOAD
# ============================================================

@app.route("/api/upload", methods=["POST"])
@require_auth
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files['file']
    session_id = request.form.get("session_id", "")
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": f"File type not allowed"}), 400
    filename = secure_filename(file.filename)
    upload_dir = "/tmp/yubilab_uploads"
    if session_id:
        s = ai_agent.get_session(session_id)
        if s:
            upload_dir = os.path.join(s.workspace_path, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, filename)
    file.save(file_path)

    if archive_extractor.is_supported(filename) and session_id:
        extract_result = archive_extractor.extract(file_path, ai_agent.get_session(session_id).workspace_path)
        if extract_result["success"]:
            ai_agent.process_uploaded_project(session_id, file_path)
            return jsonify({"success": True, "message": f"Extracted: {extract_result['file_count']} files", "file_count": extract_result["file_count"], "session_id": session_id})
        return jsonify({"error": f"Extraction failed: {extract_result['error']}"}), 500

    return jsonify({"success": True, "filename": filename, "path": file_path})


# ============================================================
# WORKSPACE ROUTES
# ============================================================

@app.route("/api/workspaces", methods=["GET"])
@require_auth
def list_workspaces():
    return jsonify({"workspaces": workspace_service.list_workspaces()})


@app.route("/api/workspaces/<workspace_id>/files", methods=["GET"])
@require_auth
def list_workspace_files(workspace_id):
    result = workspace_service.list_files(workspace_id, request.args.get("path", ""))
    return jsonify(result)


@app.route("/api/workspaces/<workspace_id>/files/<path:file_path>", methods=["GET"])
@require_auth
def read_workspace_file(workspace_id, file_path):
    result = workspace_service.read_file(workspace_id, file_path)
    if result.get("success"):
        return jsonify(result)
    return jsonify(result), 404


@app.route("/api/workspaces/<workspace_id>/files/<path:file_path>", methods=["PUT"])
@require_auth
def write_workspace_file(workspace_id, file_path):
    data = request.get_json()
    if not data or "content" not in data:
        return jsonify({"error": "Content required"}), 400
    return jsonify(workspace_service.write_file(workspace_id, file_path, data["content"]))


@app.route("/api/workspaces/<workspace_id>/files/<path:file_path>", methods=["DELETE"])
@require_auth
def delete_workspace_file(workspace_id, file_path):
    return jsonify(workspace_service.delete_file(workspace_id, file_path))


# ============================================================
# TERMINAL ROUTES
# ============================================================

@app.route("/api/terminal/sessions", methods=["POST"])
@require_auth
def create_terminal_session():
    data = request.get_json() or {}
    return jsonify(terminal_service.create_session(data.get("workspace_id", "default"))), 201


@app.route("/api/terminal/sessions", methods=["GET"])
@require_auth
def list_terminal_sessions():
    return jsonify({"sessions": terminal_service.list_sessions()})


@app.route("/api/terminal/sessions/<session_id>/execute", methods=["POST"])
@require_auth
def execute_terminal_command(session_id):
    data = request.get_json()
    if not data or not data.get("command"):
        return jsonify({"error": "Command required"}), 400
    return jsonify(terminal_service.execute_command(session_id, data["command"], data.get("timeout", 30)))


@app.route("/api/terminal/sessions/<session_id>/history", methods=["GET"])
@require_auth
def terminal_history(session_id):
    return jsonify(terminal_service.get_history(session_id))


@app.route("/api/terminal/sessions/<session_id>", methods=["DELETE"])
@require_auth
def delete_terminal_session(session_id):
    return jsonify(terminal_service.delete_session(session_id))


# ============================================================
# AI CHAT (Direct Groq)
# ============================================================

@app.route("/api/ai/chat", methods=["POST"])
@require_auth
@rate_limit(limit=30, window_seconds=60)
def ai_chat():
    data = request.get_json()
    if not data or not data.get("message"):
        return jsonify({"error": "Message required"}), 400
    messages = data.get("messages", [
        {"role": "system", "content": "You are YubiLab AI, a helpful coding assistant."},
    ])
    messages.append({"role": "user", "content": data["message"]})
    response = groq_service.chat_completion(
        messages=messages,
        temperature=data.get("temperature", 0.3),
        max_tokens=data.get("max_tokens", 8192),
    )
    return jsonify({"success": True, "response": response})


# ============================================================
# ADMIN
# ============================================================

@app.route("/api/admin/users", methods=["GET"])
@require_admin
def list_users():
    users = [{"user_id": d.get("user_id"), "username": u, "plan": d.get("plan", "free"), "email": d.get("email", "")} for u, d in users_db.items()]
    return jsonify({"users": users})


@app.route("/api/admin/stats", methods=["GET"])
@require_admin
def admin_stats():
    return jsonify({"total_users": len(users_db), "active_sessions": len(ai_agent.sessions), "active_terminals": len(terminal_service.sessions)})


# ============================================================
# WEBSOCKET - Real-time Streaming (eventlet handles this)
# ============================================================

@socketio.on("connect")
def handle_connect():
    logger.info(f"Client connected: {request.sid}")
    emit("connected", {"message": "Connected to YubiLab Agent"})


@socketio.on("disconnect")
def handle_disconnect():
    logger.info(f"Client disconnected: {request.sid}")


@socketio.on("join_session")
def handle_join_session(data):
    session_id = data.get("session_id")
    if session_id:
        join_room(session_id)
        emit("joined", {"session_id": session_id})


@socketio.on("leave_session")
def handle_leave_session(data):
    session_id = data.get("session_id")
    if session_id:
        leave_room(session_id)


@socketio.on("agent_chat")
def handle_agent_chat(data):
    """Real-time agent chat via WebSocket - SYNC."""
    session_id = data.get("session_id")
    message = data.get("message")
    if not session_id or not message:
        emit("error", {"message": "session_id and message required"})
        return

    emit("agent_thinking", {"thinking": "Processing your request..."})

    messages = [
        {"role": "system", "content": "You are YubiLab AI Agent - an autonomous software engineer."},
        {"role": "user", "content": message},
    ]

    full_response = ""
    for chunk in groq_service.stream_completion(messages):
        full_response += chunk
        emit("agent_stream", {"chunk": chunk})

    emit("agent_complete", {"response": full_response, "session_id": session_id})


@socketio.on("terminal_input")
def handle_terminal_input(data):
    session_id = data.get("session_id")
    command = data.get("command")
    if not session_id or not command:
        return
    result = terminal_service.execute_command(session_id, command)
    emit("terminal_output", result)


# ============================================================
# HEALTH & INFO
# ============================================================

@app.route("/api/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "healthy", "service": "yubilab-worker", "version": "2.1.0",
        "timestamp": datetime.utcnow().isoformat(),
        "groq_model": os.getenv("GROQ_DEFAULT_MODEL", "openai/gpt-oss-120b"),
        "active_sessions": len(ai_agent.sessions),
    })


@app.route("/api/info", methods=["GET"])
def service_info():
    return jsonify({
        "name": "YubiLab AI Agent Worker", "version": "2.1.0",
        "features": ["Autonomous Agent", "Real-time Streaming", "Part-by-part Processing",
                      "File Upload (zip/rar/tar/7z)", "Terminal", "WebSocket"],
        "groq_model": os.getenv("GROQ_DEFAULT_MODEL", "openai/gpt-oss-120b"),
    })


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(e):
    logger.error(f"Server error: {str(e)}")
    return jsonify({"error": "Internal server error"}), 500


@app.errorhandler(413)
def file_too_large(e):
    return jsonify({"error": "File too large (max 100MB)"}), 413


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    logger.info(f"Starting YubiLab Worker on {host}:{port}")
    socketio.run(app, host=host, port=port, debug=False)
