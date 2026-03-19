"""File manager for per-user directory-based file storage."""

from pathlib import Path
from typing import List


class FileManager:
    """Manages file operations scoped to a user's directory.

    All paths are resolved relative to ``base_path/user_id/`` and
    validated against path traversal attacks.
    """

    def __init__(self, base_path: str, user_id: str):
        self.base_path = Path(base_path).resolve()
        self.user_id = user_id
        self.user_dir = (self.base_path / user_id).resolve()

    def ensure_user_dir(self) -> None:
        """Create the user directory if it doesn't exist."""
        self.user_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, path: str) -> Path:
        """Resolve *path* inside the user directory, preventing traversal."""
        resolved = (self.user_dir / path).resolve()
        if not str(resolved).startswith(str(self.user_dir)):
            raise ValueError(f"Path traversal detected: {path}")
        return resolved

    def list_files(self) -> List[str]:
        """Return relative paths of all files in the user directory."""
        if not self.user_dir.exists():
            return []
        return [
            str(p.relative_to(self.user_dir))
            for p in self.user_dir.rglob("*")
            if p.is_file()
        ]

    def read_file(self, path: str) -> str:
        """Read and return file contents as text."""
        file_path = self._safe_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return file_path.read_text(encoding="utf-8")

    def _unique_path(self, path: str) -> Path:
        """Return the file path inside the user directory.

        Creates parent directories as needed. Overwrites existing files.
        """
        file_path = self._safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return file_path

    def write_file(self, path: str, content: str) -> str:
        """Write text content to a file, creating parent directories as needed.

        Overwrites existing files.

        Returns the final relative path.
        """
        file_path = self._unique_path(path)
        file_path.write_text(content, encoding="utf-8")
        return str(file_path.relative_to(self.user_dir))

    def write_bytes(self, path: str, data: bytes) -> str:
        """Write binary data to a file, creating parent directories as needed.

        Overwrites existing files.

        Returns the final relative path.
        """
        file_path = self._unique_path(path)
        file_path.write_bytes(data)
        return str(file_path.relative_to(self.user_dir))

    def delete_file(self, path: str) -> None:
        """Delete a file."""
        file_path = self._safe_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        file_path.unlink()

    def delete_user_dir(self) -> None:
        """Recursively delete the entire user directory."""
        import shutil
        if self.user_dir.exists():
            shutil.rmtree(self.user_dir)
