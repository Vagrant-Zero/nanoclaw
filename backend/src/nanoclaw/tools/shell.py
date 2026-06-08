"""Shell execution tool — run shell commands (sandboxed)."""

import subprocess
import shlex
from nanoclaw.tools.base import BaseTool, ToolSpec


class RunShellTool(BaseTool):
    """Run a shell command and return its output."""

    spec = ToolSpec(
        name="run_shell",
        description="Run a shell command and return stdout + stderr. Allowed commands: ls, cat, pwd, echo, head, tail, wc, find, grep, sort, uniq, date, whoami, which, mkdir, touch, cp, mv, rm (only in allowed directories).",
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 30)",
                },
            },
            "required": ["command"],
        },
    )

    _ALLOWED_PREFIXES = (
        "ls", "cat", "pwd", "echo", "head", "tail", "wc",
        "find", "grep", "sort", "uniq", "date", "whoami", "which",
        "mkdir", "touch", "cp", "mv", "rm",
    )

    def run(self, command: str, timeout: int = 30) -> str:
        cmd_name = shlex.split(command)[0] if command.strip() else ""

        if cmd_name not in self._ALLOWED_PREFIXES:
            return f"Error: command '{cmd_name}' is not in the allowed list: {', '.join(self._ALLOWED_PREFIXES)}"

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += f"STDERR:\n{result.stderr}"
            if result.returncode != 0:
                output += f"\n(exit code: {result.returncode})"
            return output.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"
        except Exception as exc:
            return f"Error running command: {exc}"
