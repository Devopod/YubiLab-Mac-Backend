"""
Context Manager for YubiLab AI Agent
Handles large codebases by splitting them into parts that fit within the context window.
If project code exceeds MAX_OUTPUT_SIZE (4096 tokens), processes part by part with 100% coverage.
"""

import os
import hashlib
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class CodePart:
    """Represents a chunk of code that fits within the context window."""
    index: int
    total_parts: int
    file_path: str
    content: str
    start_line: int
    end_line: int
    token_estimate: int
    checksum: str

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "total_parts": self.total_parts,
            "file_path": self.file_path,
            "content": self.content,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "token_estimate": self.token_estimate,
            "checksum": self.checksum,
        }


@dataclass
class ProjectContext:
    """Complete project context with structure and code parts."""
    project_name: str
    root_path: str
    file_tree: str
    total_files: int
    total_tokens_estimate: int
    parts: List[CodePart] = field(default_factory=list)
    file_summaries: Dict[str, str] = field(default_factory=dict)
    current_part_index: int = 0
    processed_parts: List[int] = field(default_factory=list)
    analysis_complete: bool = False

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "root_path": self.root_path,
            "file_tree": self.file_tree,
            "total_files": self.total_files,
            "total_tokens_estimate": self.total_tokens_estimate,
            "total_parts": len(self.parts),
            "current_part_index": self.current_part_index,
            "processed_parts": self.processed_parts,
            "analysis_complete": self.analysis_complete,
        }


# File extensions to include in analysis
CODE_EXTENSIONS = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.c', '.cpp', '.h', '.hpp',
    '.cs', '.go', '.rs', '.rb', '.php', '.swift', '.kt', '.scala', '.r',
    '.html', '.css', '.scss', '.sass', '.less', '.vue', '.svelte',
    '.json', '.yaml', '.yml', '.toml', '.xml', '.sql',
    '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd',
    '.dockerfile', '.makefile', '.cmake',
    '.md', '.rst', '.txt', '.env', '.gitignore', '.dockerignore',
    '.ini', '.cfg', '.conf', '.properties',
}

# Directories to skip
SKIP_DIRS = {
    'node_modules', '.git', '__pycache__', '.venv', 'venv', 'env',
    'dist', 'build', '.next', '.nuxt', 'target', 'bin', 'obj',
    '.idea', '.vscode', '.vs', 'coverage', '.coverage',
    'vendor', 'Pods', '.gradle', '.dart_tool',
    'cache', '.cache', 'tmp', 'temp',
}

# Binary/skip file patterns
SKIP_PATTERNS = {
    '.pyc', '.pyo', '.so', '.dll', '.exe', '.bin', '.dat',
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg', '.webp',
    '.mp3', '.mp4', '.avi', '.mov', '.wav', '.flac',
    '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz',
    '.woff', '.woff2', '.ttf', '.eot', '.otf',
    '.lock', '.wasm',
}

# Estimated tokens per character (rough approximation: ~4 chars per token)
CHARS_PER_TOKEN = 4


class ContextManager:
    """
    Manages codebase context for the AI agent.
    Splits large projects into manageable parts that fit within the context window.
    Tracks progress across parts to ensure 100% coverage.
    """

    def __init__(self, max_context_tokens: int = 4096):
        self.max_context_tokens = max_context_tokens
        # Reserve tokens for system prompt, instructions, and response
        self.usable_tokens = int(max_context_tokens * 0.7)  # 70% for code, 30% for prompts/response

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for a string."""
        return max(1, len(text) // CHARS_PER_TOKEN)

    def generate_checksum(self, content: str) -> str:
        """Generate MD5 checksum for content integrity."""
        return hashlib.md5(content.encode('utf-8')).hexdigest()[:12]

    def should_include_file(self, file_path: str) -> bool:
        """Check if a file should be included in the analysis."""
        path = Path(file_path)
        name = path.name.lower()

        # Skip hidden files (but include .env, .gitignore, etc.)
        if name.startswith('.') and name not in {'.env', '.gitignore', '.dockerignore', '.eslintrc', '.prettierrc', '.babelrc', '.editorconfig'}:
            return False

        # Skip binary and media files
        suffix = path.suffix.lower()
        if suffix in SKIP_PATTERNS:
            return False

        # Skip lock files and generated files
        if name.endswith('.lock') or name.endswith('.min.js') or name.endswith('.min.css'):
            return False

        # Include code files
        if suffix in CODE_EXTENSIONS:
            return True

        # Include files without extensions (like Makefile, Dockerfile, etc.)
        if not suffix and name in {'makefile', 'dockerfile', 'vagrantfile', 'rakefile', 'gemfile', 'procfile', 'readme', 'license'}:
            return True

        # Include common config files
        if name in {'.env', '.env.local', '.env.production', '.env.development', 'package.json', 'tsconfig.json', 'pyproject.toml', 'requirements.txt', 'cargo.toml', 'go.mod'}:
            return True

        return False

    def should_skip_directory(self, dir_name: str) -> bool:
        """Check if a directory should be skipped."""
        return dir_name.lower() in SKIP_DIRS or dir_name.startswith('.')

    def build_file_tree(self, root_path: str, max_depth: int = 4) -> Tuple[str, List[str]]:
        """Build a visual file tree and return it with list of code file paths."""
        tree_lines = []
        code_files = []
        root = Path(root_path)

        def walk(directory: Path, prefix: str = "", depth: int = 0):
            if depth > max_depth:
                tree_lines.append(f"{prefix}... (max depth reached)")
                return

            try:
                entries = sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            except PermissionError:
                return

            dirs = [e for e in entries if e.is_dir() and not self.should_skip_directory(e.name)]
            files = [e for e in entries if e.is_file()]

            for i, entry in enumerate(dirs):
                is_last = (i == len(dirs) - 1) and not files
                connector = "└── " if is_last else "├── "
                tree_lines.append(f"{prefix}{connector}{entry.name}/")
                extension = "    " if is_last else "│   "
                walk(entry, prefix + extension, depth + 1)

            for i, entry in enumerate(files):
                is_last = i == len(files) - 1
                connector = "└── " if is_last else "├── "
                tree_lines.append(f"{prefix}{connector}{entry.name}")

                if self.should_include_file(str(entry)):
                    code_files.append(str(entry))

        tree_lines.append(f"{root.name}/")
        walk(root, "")
        return "\n".join(tree_lines), code_files

    def read_file_content(self, file_path: str) -> Optional[str]:
        """Read file content with error handling."""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            return content
        except (IOError, OSError):
            return None

    def split_file_into_parts(self, file_path: str, content: str) -> List[CodePart]:
        """Split a large file into parts that fit within the context window."""
        lines = content.split('\n')
        parts = []
        current_lines = []
        current_tokens = 0
        start_line = 1

        for i, line in enumerate(lines, 1):
            line_tokens = self.estimate_tokens(line)

            if current_tokens + line_tokens > self.usable_tokens and current_lines:
                # Current part is full, save it
                part_content = '\n'.join(current_lines)
                parts.append(CodePart(
                    index=len(parts),
                    total_parts=0,  # Will be updated later
                    file_path=file_path,
                    content=part_content,
                    start_line=start_line,
                    end_line=i - 1,
                    token_estimate=current_tokens,
                    checksum=self.generate_checksum(part_content),
                ))
                current_lines = [line]
                current_tokens = line_tokens
                start_line = i
            else:
                current_lines.append(line)
                current_tokens += line_tokens

        # Don't forget the last part
        if current_lines:
            part_content = '\n'.join(current_lines)
            parts.append(CodePart(
                index=len(parts),
                total_parts=0,
                file_path=file_path,
                content=part_content,
                start_line=start_line,
                end_line=len(lines),
                token_estimate=current_tokens,
                checksum=self.generate_checksum(part_content),
            ))

        # Update total_parts
        for part in parts:
            part.total_parts = len(parts)

        return parts

    def create_project_context(self, root_path: str, project_name: str = None) -> ProjectContext:
        """
        Create a complete project context, splitting into parts as needed.
        This is the main entry point for analyzing a project.
        """
        if project_name is None:
            project_name = Path(root_path).name

        # Build file tree
        file_tree, code_files = self.build_file_tree(root_path)

        # Collect all code content
        all_parts = []
        total_tokens = 0
        file_summaries = {}

        for file_path in code_files:
            content = self.read_file_content(file_path)
            if content is None:
                continue

            file_tokens = self.estimate_tokens(content)
            rel_path = os.path.relpath(file_path, root_path)
            file_summaries[rel_path] = f"~{file_tokens} tokens, {len(content.splitlines())} lines"

            if file_tokens <= self.usable_tokens:
                # File fits in one part
                all_parts.append(CodePart(
                    index=len(all_parts),
                    total_parts=0,
                    file_path=rel_path,
                    content=content,
                    start_line=1,
                    end_line=len(content.splitlines()),
                    token_estimate=file_tokens,
                    checksum=self.generate_checksum(content),
                ))
            else:
                # File needs to be split
                file_parts = self.split_file_into_parts(rel_path, content)
                for part in file_parts:
                    part.index = len(all_parts)
                    all_parts.append(part)

            total_tokens += file_tokens

        # Update total_parts for all parts
        for part in all_parts:
            part.total_parts = len(all_parts)

        return ProjectContext(
            project_name=project_name,
            root_path=root_path,
            file_tree=file_tree,
            total_files=len(code_files),
            total_tokens_estimate=total_tokens,
            parts=all_parts,
            file_summaries=file_summaries,
        )

    def get_next_part(self, context: ProjectContext) -> Optional[CodePart]:
        """Get the next unprocessed part from the context."""
        for i, part in enumerate(context.parts):
            if i not in context.processed_parts:
                context.current_part_index = i
                return part
        return None

    def mark_part_processed(self, context: ProjectContext, part_index: int):
        """Mark a part as processed."""
        if part_index not in context.processed_parts:
            context.processed_parts.append(part_index)

        # Check if all parts are processed
        if len(context.processed_parts) >= len(context.parts):
            context.analysis_complete = True

    def get_context_for_prompt(self, context: ProjectContext, part: CodePart) -> str:
        """Build the context string for an AI prompt with a specific part."""
        context_str = f"""# Project: {context.project_name}

## File Structure
```
{context.file_tree}
```

## Project Statistics
- Total Files: {context.total_files}
- Estimated Total Tokens: {context.total_tokens_estimate}
- Current Part: {part.index + 1} of {len(context.parts)}
- Files Analyzed So Far: {len(context.processed_parts)} of {len(context.parts)}

## Current File: {part.file_path} (Lines {part.start_line}-{part.end_line})
```{self._get_language_hint(part.file_path)}
{part.content}
```
"""
        return context_str

    def _get_language_hint(self, file_path: str) -> str:
        """Get language hint for code block based on file extension."""
        ext_map = {
            '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
            '.jsx': 'jsx', '.tsx': 'tsx', '.html': 'html', '.css': 'css',
            '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml', '.sql': 'sql',
            '.sh': 'bash', '.java': 'java', '.c': 'c', '.cpp': 'cpp',
            '.go': 'go', '.rs': 'rust', '.rb': 'ruby', '.php': 'php',
            '.swift': 'swift', '.kt': 'kotlin', '.vue': 'vue', '.svelte': 'svelte',
        }
        ext = Path(file_path).suffix.lower()
        return ext_map.get(ext, '')

    def get_progress(self, context: ProjectContext) -> Dict:
        """Get analysis progress information."""
        total = len(context.parts)
        processed = len(context.processed_parts)
        progress_pct = (processed / total * 100) if total > 0 else 0

        return {
            "total_parts": total,
            "processed_parts": processed,
            "progress_percent": round(progress_pct, 1),
            "current_file": context.parts[context.current_part_index].file_path if context.current_part_index < total else None,
            "analysis_complete": context.analysis_complete,
        }
