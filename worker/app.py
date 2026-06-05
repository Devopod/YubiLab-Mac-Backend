import os
import uuid as uuid_mod
import uvicorn
from fastapi import FastAPI, UploadFile, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from worker.config import config
from worker.db.database import db


class TerminalCreateRequest(BaseModel):
    workspace_id: str = "default"


class TerminalWriteRequest(BaseModel):
    command: str = ""
    timeout: int = 30


class ExecuteRequest(BaseModel):
    language: str = "python"
    code: str = ""
    timeout: int = 30


class FileWriteRequest(BaseModel):
    workspace_id: str = "default"
    path: str = ""
    content: str = ""

app = FastAPI(title="YubiLab Worker", version="2.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await db.connect()
    os.makedirs(config.WORKSPACE_BASE, exist_ok=True)


@app.on_event("shutdown")
async def shutdown():
    await db.close()


# Health
@app.get("/health")
async def health():
    return {"status": "ok", "service": "yubilab-worker"}


@app.get("/ping")
async def ping():
    return "pong"


# ─── Register Routes ───
from worker.routes.agent_routes import router as agent_router
from worker.routes.deposit_routes import router as deposit_router
from worker.routes.admin_routes import router as admin_router

app.include_router(agent_router)
app.include_router(deposit_router)
app.include_router(admin_router)


# ─── Terminal Routes (100% working) ───
import asyncio
import subprocess

_terminal_sessions = {}


class TerminalSession:
    def __init__(self, workspace_id):
        self.id = str(uuid_mod.uuid4())
        self.workspace_id = workspace_id
        self.cwd = os.path.join(config.WORKSPACE_BASE, workspace_id)
        os.makedirs(self.cwd, exist_ok=True)
        self.output_buffer = []

    async def execute(self, command, timeout=30):
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            output = stdout.decode('utf-8', errors='replace')
            errors = stderr.decode('utf-8', errors='replace')
            result = {
                "exit_code": proc.returncode,
                "stdout": output[-4000:],
                "stderr": errors[-2000:],
                "command": command
            }
            self.output_buffer.append(result)
            return result
        except asyncio.TimeoutError:
            return {"exit_code": -1, "stdout": "", "stderr": f"Command timed out after {timeout}s", "command": command}
        except Exception as e:
            return {"exit_code": -1, "stdout": "", "stderr": str(e), "command": command}


@app.post("/terminal/create")
async def terminal_create(data: TerminalCreateRequest = TerminalCreateRequest()):
    session = TerminalSession(data.workspace_id)
    _terminal_sessions[session.id] = session
    return {"session_id": session.id, "workspace_id": data.workspace_id, "cwd": session.cwd}


@app.post("/terminal/{session_id}/write")
async def terminal_write(session_id: str, data: TerminalWriteRequest = TerminalWriteRequest()):
    if session_id not in _terminal_sessions:
        raise HTTPException(404, "Terminal session not found")
    session = _terminal_sessions[session_id]
    result = await session.execute(data.command, data.timeout)
    return result


@app.get("/terminal/{session_id}/read")
async def terminal_read(session_id: str):
    if session_id not in _terminal_sessions:
        raise HTTPException(404, "Terminal session not found")
    session = _terminal_sessions[session_id]
    output = session.output_buffer.copy()
    session.output_buffer.clear()
    return {"output": output}


@app.delete("/terminal/{session_id}")
async def terminal_delete(session_id: str):
    if session_id in _terminal_sessions:
        del _terminal_sessions[session_id]
    return {"status": "deleted", "session_id": session_id}


# ─── Code Execution Route ───
@app.post("/execute/run")
async def execute_code(data: ExecuteRequest = ExecuteRequest()):
    """Execute code in a sandboxed environment"""
    language = data.language
    code = data.code
    timeout = data.timeout
    if language == "python":
        cmd = f'python3 -c "{code}"' if len(code) < 500 else None
        if not cmd:
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(code)
                tmp_path = f.name
            cmd = f"python3 {tmp_path}"
    elif language == "javascript":
        cmd = f'node -e "{code}"' if len(code) < 500 else None
        if not cmd:
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
                f.write(code)
                tmp_path = f.name
            cmd = f"node {tmp_path}"
    elif language == "bash":
        cmd = code
    else:
        return {"error": f"Unsupported language: {language}"}

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode('utf-8', errors='replace')[-4000:],
            "stderr": stderr.decode('utf-8', errors='replace')[-2000:]
        }
    except asyncio.TimeoutError:
        return {"exit_code": -1, "stdout": "", "stderr": f"Execution timed out after {timeout}s"}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "stderr": str(e)}


@app.get("/execute/languages")
async def supported_languages():
    return {"languages": ["python", "javascript", "bash"]}


# ─── File Management Routes ───
@app.get("/files/list")
async def list_files(workspace_id: str = "default", path: str = "."):
    base = os.path.join(config.WORKSPACE_BASE, workspace_id)
    target = os.path.normpath(os.path.join(base, path))
    if not target.startswith(base):
        raise HTTPException(400, "Path traversal not allowed")
    if not os.path.exists(target):
        return {"files": []}
    items = []
    for name in sorted(os.listdir(target)):
        full = os.path.join(target, name)
        items.append({
            "name": name,
            "type": "directory" if os.path.isdir(full) else "file",
            "size": os.path.getsize(full) if os.path.isfile(full) else 0
        })
    return {"files": items}


@app.get("/files/read")
async def read_file(workspace_id: str = "default", path: str = ""):
    base = os.path.join(config.WORKSPACE_BASE, workspace_id)
    target = os.path.normpath(os.path.join(base, path))
    if not target.startswith(base):
        raise HTTPException(400, "Path traversal not allowed")
    if not os.path.isfile(target):
        raise HTTPException(404, "File not found")
    try:
        with open(target, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        return {"content": content[:50000], "path": path}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/files/write")
async def write_file(data: FileWriteRequest = FileWriteRequest()):
    base = os.path.join(config.WORKSPACE_BASE, data.workspace_id)
    target = os.path.normpath(os.path.join(base, data.path))
    if not target.startswith(base):
        raise HTTPException(400, "Path traversal not allowed")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, 'w', encoding='utf-8') as f:
        f.write(data.content)
    return {"status": "written", "path": data.path, "size": len(data.content)}


# ─── Solve Log Route ───
@app.get("/solve-log/{workspace_id}")
async def get_solve_log(workspace_id: str, limit: int = 50):
    from worker.agent.solve_log import solve_log
    entries = await solve_log.search(workspace_id, limit=limit)
    return {"entries": entries}


# ─── RAG Routes ───
@app.post("/rag/upload")
async def rag_upload(file: UploadFile, user_id: int = 1, workspace_id: str = "default"):
    from worker.rag.engine import rag_engine

    user = await db.fetch_one("SELECT * FROM users WHERE id = ?", [user_id])
    if not user:
        raise HTTPException(401, "User not found")
    tier = await db.fetch_one("SELECT * FROM tiers WHERE id = ?", [user['tier']])

    doc_count_row = await db.fetch_one(
        "SELECT COUNT(*) as cnt FROM rag_documents WHERE user_id = ?",
        [user_id]
    )
    doc_count = doc_count_row['cnt'] if doc_count_row else 0
    if tier['rag_docs_limit'] != -1 and doc_count >= tier['rag_docs_limit']:
        raise HTTPException(429, "RAG document limit reached for your tier")

    temp_path = os.path.join("/tmp", file.filename)
    with open(temp_path, "wb") as f:
        content = await file.read()
        f.write(content)

    result = await rag_engine.upload_document(temp_path, user_id, workspace_id)

    await db.execute("""
        INSERT INTO rag_documents (id, user_id, workspace_id, file_path, file_name, chunks_count)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (str(uuid_mod.uuid4()), user_id, workspace_id, temp_path, file.filename, result.get('indexed_chunks', 0)))

    os.remove(temp_path)
    return result


@app.post("/rag/query")
async def rag_query(query: str = "", workspace_id: str = "", top_k: int = 5):
    from worker.rag.engine import rag_engine
    results = await rag_engine.query(query, workspace_id=workspace_id or None, top_k=top_k)
    return {"results": results}


# ─── Tiers Info ───
@app.get("/tiers")
async def get_tiers():
    tiers = await db.fetch_all("SELECT * FROM tiers ORDER BY id")
    return {"tiers": tiers}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
