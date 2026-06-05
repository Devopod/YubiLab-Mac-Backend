"""
Workspace Management Service
Manages user workspaces, file operations, and project persistence.
"""

import os
import json
import shutil
import hashlib
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime


class WorkspaceService:
    """Manages workspace directories for the AI agent."""

    def __init__(self, base_path: str = "/tmp/yubilab_workspaces"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def create_workspace(self, workspace_id: str, template: str = None) -> Dict:
        """Create a new workspace directory."""
        ws_path = self.base_path / workspace_id

        if ws_path.exists():
            return {"success": False, "error": "Workspace already exists"}

        ws_path.mkdir(parents=True, exist_ok=True)

        # Apply template if specified
        if template == "python":
            self._apply_python_template(ws_path)
        elif template == "node":
            self._apply_node_template(ws_path)
        elif template == "flask":
            self._apply_flask_template(ws_path)
        elif template == "nextjs":
            self._apply_nextjs_template(ws_path)

        return {
            "success": True,
            "workspace_id": workspace_id,
            "path": str(ws_path),
        }

    def get_workspace_path(self, workspace_id: str) -> Optional[str]:
        """Get the path to a workspace."""
        ws_path = self.base_path / workspace_id
        if ws_path.exists():
            return str(ws_path)
        return None

    def list_workspaces(self) -> List[Dict]:
        """List all workspaces with metadata."""
        workspaces = []
        for entry in self.base_path.iterdir():
            if entry.is_dir() and not entry.name.startswith('.'):
                file_count = sum(1 for _ in entry.rglob('*') if _.is_file())
                stat = entry.stat()
                workspaces.append({
                    "workspace_id": entry.name,
                    "path": str(entry),
                    "file_count": file_count,
                    "size_bytes": sum(f.stat().st_size for f in entry.rglob('*') if f.is_file()),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
        return workspaces

    def delete_workspace(self, workspace_id: str) -> Dict:
        """Delete a workspace."""
        ws_path = self.base_path / workspace_id
        if not ws_path.exists():
            return {"success": False, "error": "Workspace not found"}

        shutil.rmtree(ws_path, ignore_errors=True)
        return {"success": True, "workspace_id": workspace_id}

    def list_files(self, workspace_id: str, sub_path: str = "") -> Dict:
        """List files in a workspace directory."""
        ws_path = self.base_path / workspace_id / sub_path

        if not ws_path.exists() or not ws_path.is_dir():
            return {"success": False, "error": "Directory not found"}

        entries = []
        for entry in sorted(ws_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if entry.name.startswith('.') and entry.name not in {'.env', '.gitignore', '.dockerignore'}:
                continue

            stat = entry.stat()
            entries.append({
                "name": entry.name,
                "path": str(entry.relative_to(self.base_path / workspace_id)),
                "is_directory": entry.is_dir(),
                "size": stat.st_size if entry.is_file() else 0,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })

        return {
            "success": True,
            "path": sub_path,
            "entries": entries,
        }

    def read_file(self, workspace_id: str, file_path: str) -> Dict:
        """Read a file from the workspace."""
        full_path = self.base_path / workspace_id / file_path

        # Security: prevent path traversal
        try:
            full_path.resolve().relative_to((self.base_path / workspace_id).resolve())
        except ValueError:
            return {"success": False, "error": "Invalid path"}

        if not full_path.exists():
            return {"success": False, "error": "File not found"}

        if full_path.is_dir():
            return {"success": False, "error": "Path is a directory"}

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()

            return {
                "success": True,
                "file_path": file_path,
                "content": content,
                "size": len(content),
                "lines": len(content.splitlines()),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def write_file(self, workspace_id: str, file_path: str, content: str) -> Dict:
        """Write a file to the workspace."""
        full_path = self.base_path / workspace_id / file_path

        # Security: prevent path traversal
        try:
            full_path.resolve().relative_to((self.base_path / workspace_id).resolve())
        except ValueError:
            return {"success": False, "error": "Invalid path"}

        full_path.parent.mkdir(parents=True, exist_ok=True)

        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return {
            "success": True,
            "file_path": file_path,
            "size": len(content),
        }

    def delete_file(self, workspace_id: str, file_path: str) -> Dict:
        """Delete a file from the workspace."""
        full_path = self.base_path / workspace_id / file_path

        try:
            full_path.resolve().relative_to((self.base_path / workspace_id).resolve())
        except ValueError:
            return {"success": False, "error": "Invalid path"}

        if not full_path.exists():
            return {"success": False, "error": "File not found"}

        if full_path.is_dir():
            shutil.rmtree(full_path, ignore_errors=True)
        else:
            full_path.unlink()

        return {"success": True, "file_path": file_path}

    def create_directory(self, workspace_id: str, dir_path: str) -> Dict:
        """Create a directory in the workspace."""
        full_path = self.base_path / workspace_id / dir_path

        try:
            full_path.resolve().relative_to((self.base_path / workspace_id).resolve())
        except ValueError:
            return {"success": False, "error": "Invalid path"}

        full_path.mkdir(parents=True, exist_ok=True)
        return {"success": True, "dir_path": dir_path}

    def _apply_python_template(self, ws_path: Path):
        """Create a basic Python project template."""
        (ws_path / "main.py").write_text('# YubiLab Python Project\n\ndef main():\n    print("Hello, World!")\n\nif __name__ == "__main__":\n    main()\n')
        (ws_path / "requirements.txt").write_text('# Add your dependencies here\n')
        (ws_path / "README.md").write_text('# Python Project\n\nCreated with YubiLab AI Agent.\n')

    def _apply_node_template(self, ws_path: Path):
        """Create a basic Node.js project template."""
        (ws_path / "index.js").write_text('// YubiLab Node.js Project\n\nconsole.log("Hello, World!");\n')
        (ws_path / "package.json").write_text('{\n  "name": "yubilab-project",\n  "version": "1.0.0",\n  "main": "index.js"\n}\n')

    def _apply_flask_template(self, ws_path: Path):
        """Create a basic Flask project template."""
        (ws_path / "app.py").write_text('from flask import Flask\n\napp = Flask(__name__)\n\n@app.route("/")\ndef hello():\n    return "Hello from YubiLab!"\n\nif __name__ == "__main__":\n    app.run(debug=True)\n')
        (ws_path / "requirements.txt").write_text('flask\n')

    def _apply_nextjs_template(self, ws_path: Path):
        """Create a basic Next.js project template."""
        (ws_path / "package.json").write_text('{\n  "name": "yubilab-nextjs",\n  "version": "1.0.0",\n  "scripts": {\n    "dev": "next dev",\n    "build": "next build",\n    "start": "next start"\n  }\n}\n')
        app_dir = ws_path / "app"
        app_dir.mkdir(exist_ok=True)
        (app_dir / "page.js").write_text('export default function Home() {\n  return <h1>Hello from YubiLab!</h1>;\n}\n')
