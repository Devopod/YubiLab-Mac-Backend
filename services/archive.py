"""
Archive Extraction Service - Handles zip, rar, tar, 7z and other archive formats.
Extracts uploaded project archives into workspaces for AI processing.
"""

import os
import zipfile
import tarfile
import subprocess
import shutil
from pathlib import Path
from typing import Dict, Optional


class ArchiveExtractor:
    """
    Extracts various archive formats into workspace directories.
    Supports: .zip, .tar, .tar.gz, .tgz, .tar.bz2, .tbz2, .tar.xz, .7z, .rar
    """

    SUPPORTED_EXTENSIONS = {
        '.zip', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2',
        '.tar.xz', '.txz', '.7z', '.rar',
    }

    def is_supported(self, filename: str) -> bool:
        """Check if the file format is supported."""
        lower = filename.lower()
        for ext in self.SUPPORTED_EXTENSIONS:
            if lower.endswith(ext):
                return True
        return False

    def get_archive_type(self, filename: str) -> Optional[str]:
        """Determine the archive type from filename."""
        lower = filename.lower()
        if lower.endswith('.zip'):
            return 'zip'
        elif lower.endswith('.tar.gz') or lower.endswith('.tgz'):
            return 'tar.gz'
        elif lower.endswith('.tar.bz2') or lower.endswith('.tbz2'):
            return 'tar.bz2'
        elif lower.endswith('.tar.xz') or lower.endswith('.txz'):
            return 'tar.xz'
        elif lower.endswith('.tar'):
            return 'tar'
        elif lower.endswith('.7z'):
            return '7z'
        elif lower.endswith('.rar'):
            return 'rar'
        return None

    def extract(self, archive_path: str, destination: str) -> Dict:
        """
        Extract an archive to the destination directory.
        Returns a dict with success status, file count, and any errors.
        """
        if not os.path.exists(archive_path):
            return {"success": False, "error": f"Archive not found: {archive_path}"}

        archive_type = self.get_archive_type(archive_path)
        if not archive_type:
            return {"success": False, "error": f"Unsupported archive format: {archive_path}"}

        os.makedirs(destination, exist_ok=True)

        try:
            if archive_type == 'zip':
                return self._extract_zip(archive_path, destination)
            elif archive_type.startswith('tar'):
                return self._extract_tar(archive_path, destination, archive_type)
            elif archive_type == '7z':
                return self._extract_7z(archive_path, destination)
            elif archive_type == 'rar':
                return self._extract_rar(archive_path, destination)
            else:
                return {"success": False, "error": f"Unknown archive type: {archive_type}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _extract_zip(self, archive_path: str, destination: str) -> Dict:
        """Extract a ZIP archive."""
        try:
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                # Security check: prevent path traversal
                for member in zip_ref.namelist():
                    if member.startswith('/') or '..' in member:
                        continue

                # Count files before extraction
                file_count = len([m for m in zip_ref.namelist() if not m.endswith('/')])

                # Extract
                zip_ref.extractall(destination)

                # Flatten if single top-level directory
                self._flatten_if_needed(destination)

                return {
                    "success": True,
                    "file_count": file_count,
                    "archive_type": "zip",
                    "destination": destination,
                }
        except zipfile.BadZipFile:
            return {"success": False, "error": "Invalid or corrupted ZIP file"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _extract_tar(self, archive_path: str, destination: str, archive_type: str) -> Dict:
        """Extract a TAR archive (with optional compression)."""
        mode_map = {
            'tar': 'r',
            'tar.gz': 'r:gz',
            'tar.bz2': 'r:bz2',
            'tar.xz': 'r:xz',
        }
        mode = mode_map.get(archive_type, 'r')

        try:
            with tarfile.open(archive_path, mode) as tar_ref:
                # Security: prevent path traversal
                members = []
                for member in tar_ref.getmembers():
                    if member.name.startswith('/') or '..' in member.name:
                        continue
                    members.append(member)

                file_count = len([m for m in members if m.isfile()])

                tar_ref.extractall(destination, members=members)

                self._flatten_if_needed(destination)

                return {
                    "success": True,
                    "file_count": file_count,
                    "archive_type": archive_type,
                    "destination": destination,
                }
        except tarfile.TarError as e:
            return {"success": False, "error": f"Invalid TAR archive: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _extract_7z(self, archive_path: str, destination: str) -> Dict:
        """Extract a 7z archive using 7z command."""
        try:
            result = subprocess.run(
                ['7z', 'x', archive_path, f'-o{destination}', '-y'],
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                # Try p7zip
                result = subprocess.run(
                    ['7za', 'x', archive_path, f'-o{destination}', '-y'],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

            if result.returncode != 0:
                return {"success": False, "error": f"7z extraction failed: {result.stderr}"}

            # Count extracted files
            file_count = sum(1 for _, _, files in os.walk(destination) for f in files)

            self._flatten_if_needed(destination)

            return {
                "success": True,
                "file_count": file_count,
                "archive_type": "7z",
                "destination": destination,
            }
        except FileNotFoundError:
            return {"success": False, "error": "7z not installed. Install p7zip-full."}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "7z extraction timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _extract_rar(self, archive_path: str, destination: str) -> Dict:
        """Extract a RAR archive using unrar command."""
        try:
            result = subprocess.run(
                ['unrar', 'x', '-y', archive_path, destination],
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                return {"success": False, "error": f"RAR extraction failed: {result.stderr}"}

            file_count = sum(1 for _, _, files in os.walk(destination) for f in files)

            self._flatten_if_needed(destination)

            return {
                "success": True,
                "file_count": file_count,
                "archive_type": "rar",
                "destination": destination,
            }
        except FileNotFoundError:
            return {"success": False, "error": "unrar not installed. Install unrar."}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "RAR extraction timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _flatten_if_needed(self, destination: str):
        """
        If the archive contained a single top-level directory,
        move its contents up one level for easier access.
        """
        entries = os.listdir(destination)

        if len(entries) == 1:
            single_entry = os.path.join(destination, entries[0])
            if os.path.isdir(single_entry):
                # Move contents up
                temp_dir = destination + "_temp"
                os.rename(single_entry, temp_dir)
                shutil.rmtree(destination, ignore_errors=True)
                os.makedirs(destination, exist_ok=True)

                for item in os.listdir(temp_dir):
                    src = os.path.join(temp_dir, item)
                    dst = os.path.join(destination, item)
                    shutil.move(src, dst)

                os.rmdir(temp_dir)
