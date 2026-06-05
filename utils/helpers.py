"""
Helper utilities for YubiLab Backend.
"""

import os
import json
import re
from datetime import datetime
from typing import Dict, List, Any, Optional


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename to prevent path traversal and invalid characters."""
    # Remove path separators and null bytes
    filename = filename.replace('/', '_').replace('\\', '_').replace('\0', '')
    # Remove leading dots (hidden files)
    filename = filename.lstrip('.')
    # Limit length
    if len(filename) > 255:
        name, ext = os.path.splitext(filename)
        filename = name[:250] + ext
    return filename


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def detect_language(file_path: str) -> str:
    """Detect programming language from file extension."""
    ext_map = {
        '.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript',
        '.jsx': 'React JSX', '.tsx': 'React TSX', '.html': 'HTML',
        '.css': 'CSS', '.scss': 'SCSS', '.java': 'Java',
        '.c': 'C', '.cpp': 'C++', '.cs': 'C#', '.go': 'Go',
        '.rs': 'Rust', '.rb': 'Ruby', '.php': 'PHP',
        '.swift': 'Swift', '.kt': 'Kotlin', '.scala': 'Scala',
        '.r': 'R', '.sql': 'SQL', '.sh': 'Shell', '.bash': 'Bash',
        '.json': 'JSON', '.yaml': 'YAML', '.yml': 'YAML',
        '.xml': 'XML', '.md': 'Markdown', '.vue': 'Vue',
        '.svelte': 'Svelte', '.dart': 'Dart', '.lua': 'Lua',
        '.pl': 'Perl', '.ex': 'Elixir', '.erl': 'Erlang',
        '.hs': 'Haskell', '.ml': 'OCaml', '.clj': 'Clojure',
    }
    ext = os.path.splitext(file_path)[1].lower()
    return ext_map.get(ext, 'Unknown')


def truncate_output(output: str, max_length: int = 8100) -> str:
    """Truncate output to fit within size limits."""
    if len(output) <= max_length:
        return output
    return output[:max_length] + f"\n... (truncated, {len(output)} total bytes)"


def safe_json_parse(text: str) -> Optional[Dict]:
    """Safely parse JSON from text, handling code blocks."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from code block
    pattern = r'```(?:json)?\s*\n?(.*?)\n?```'
    matches = re.findall(pattern, text, re.DOTALL)
    for match in matches:
        try:
            return json.loads(match.strip())
        except json.JSONDecodeError:
            continue

    return None


def get_project_summary(workspace_path: str) -> Dict:
    """Generate a summary of a project workspace."""
    if not os.path.exists(workspace_path):
        return {"error": "Workspace not found"}

    total_files = 0
    total_dirs = 0
    total_size = 0
    languages = {}
    largest_files = []

    for root, dirs, files in os.walk(workspace_path):
        # Skip hidden and build directories
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in {
            'node_modules', '__pycache__', 'venv', '.venv', 'dist', 'build', '.git'
        }]

        total_dirs += len(dirs)

        for f in files:
            file_path = os.path.join(root, f)
            total_files += 1

            try:
                size = os.path.getsize(file_path)
                total_size += size

                # Detect language
                lang = detect_language(f)
                if lang != 'Unknown':
                    languages[lang] = languages.get(lang, 0) + 1

                # Track largest files
                largest_files.append({
                    "path": os.path.relpath(file_path, workspace_path),
                    "size": size,
                })
            except OSError:
                continue

    # Sort and limit largest files
    largest_files.sort(key=lambda x: x["size"], reverse=True)
    largest_files = largest_files[:10]

    return {
        "total_files": total_files,
        "total_dirs": total_dirs,
        "total_size": total_size,
        "total_size_human": format_file_size(total_size),
        "languages": languages,
        "largest_files": largest_files,
    }
