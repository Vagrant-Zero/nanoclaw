"""File operations tool — read files from the filesystem."""

from pathlib import Path

from nanoclaw.tools.base import BaseTool, ToolSpec


class ReadFileTool(BaseTool):
    """Read the contents of a file."""

    spec = ToolSpec(
        name="read_file",
        description="Read the contents of a file at the given path.",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to read",
                }
            },
            "required": ["file_path"],
        },
    )

    def run(self, file_path: str) -> str:
        try:
            content = Path(file_path).read_text(encoding="utf-8")
            return content
        except FileNotFoundError:
            return f"Error: file not found: {file_path}"
        except PermissionError:
            return f"Error: permission denied: {file_path}"
        except Exception as exc:
            return f"Error reading file: {exc}"
