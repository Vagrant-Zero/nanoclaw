"""Base tool abstractions: ToolSpec and BaseTool."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolSpec:
    """Specification for a tool — name, description, and JSON schema for parameters."""

    name: str
    description: str
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


class BaseTool(ABC):
    """Abstract base class for all tools."""

    spec: ToolSpec

    @abstractmethod
    def run(self, **kwargs) -> str:
        """Execute the tool with the given keyword arguments."""
        ...
