import aiosqlite
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "yubilab.db")


class Database:
    def __init__(self):
        self.db = None

    async def connect(self):
        self.db = await aiosqlite.connect(DB_PATH)
        self.db.row_factory = aiosqlite.Row
        await self._migrate()

    async def close(self):
        if self.db:
            await self.db.close()

    async def _migrate(self):
        import bcrypt

        # Users
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                tier INTEGER DEFAULT 1,
                is_admin INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                ai_prompts_used_today INTEGER DEFAULT 0,
                apk_builds_used_today INTEGER DEFAULT 0,
                terminal_minutes_used REAL DEFAULT 0,
                last_reset_date TEXT DEFAULT '',
                storage_used_mb REAL DEFAULT 0
            )
        """)

        # Tiers
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS tiers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                ai_prompts_daily INTEGER NOT NULL,
                apk_builds_daily INTEGER NOT NULL,
                max_sub_agents INTEGER NOT NULL,
                terminal_minutes_daily REAL NOT NULL,
                max_upload_mb INTEGER NOT NULL,
                rag_docs_limit INTEGER NOT NULL,
                storage_mb INTEGER NOT NULL,
                price_bdt INTEGER NOT NULL
            )
        """)

        await self.db.execute("""
            INSERT OR IGNORE INTO tiers VALUES
            (1, 'Free', 3, 1, 1, 30, 5, 5, 50, 0),
            (2, 'Pro', 500, 30, 5, 480, 50, 100, 5000, 1200),
            (3, 'Enterprise', -1, -1, 10, 1440, 500, -1, 50000, 5000)
        """)

        # bKash Deposits
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                trx_id TEXT NOT NULL,
                screenshot_path TEXT NOT NULL,
                target_tier INTEGER NOT NULL DEFAULT 2,
                status TEXT DEFAULT 'pending',
                admin_note TEXT DEFAULT '',
                reviewed_by INTEGER,
                reviewed_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (reviewed_by) REFERENCES users(id)
            )
        """)

        # Solve Log
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS solve_log (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                problem_type TEXT,
                problem_description TEXT,
                approach TEXT,
                tool_calls TEXT,
                result_summary TEXT,
                error TEXT,
                success INTEGER,
                agent_id TEXT,
                parent_agent_id TEXT,
                files_modified TEXT,
                commands_run TEXT,
                duration_ms INTEGER DEFAULT 0
            )
        """)

        # Agent Runs
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                workspace_id TEXT NOT NULL,
                prompt TEXT NOT NULL,
                mode TEXT DEFAULT 'autonomous',
                status TEXT DEFAULT 'running',
                current_step TEXT,
                steps_total INTEGER DEFAULT 0,
                steps_completed INTEGER DEFAULT 0,
                sub_agents_active INTEGER DEFAULT 0,
                sub_agents_max INTEGER DEFAULT 1,
                result_summary TEXT,
                error TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        # RAG Documents
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS rag_documents (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                workspace_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                chunks_count INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        # Indexes
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_deposits_status ON deposits(status)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_deposits_user ON deposits(user_id)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_solve_workspace ON solve_log(workspace_id)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_solve_success ON solve_log(success)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_user ON agent_runs(user_id)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_rag_docs_user ON rag_documents(user_id)")

        # Create default admin if not exists
        pw_hash = bcrypt.hashpw(b'admin123', bcrypt.gensalt()).decode()
        await self.db.execute("""
            INSERT OR IGNORE INTO users (username, email, password_hash, tier, is_admin)
            VALUES ('admin', 'admin@yubilab.com', ?, 3, 1)
        """, (pw_hash,))

        await self.db.commit()

    async def execute(self, query, params=None):
        cursor = await self.db.execute(query, params or [])
        await self.db.commit()
        return cursor

    async def fetch_one(self, query, params=None):
        cursor = await self.db.execute(query, params or [])
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, query, params=None):
        cursor = await self.db.execute(query, params or [])
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


db = Database()
