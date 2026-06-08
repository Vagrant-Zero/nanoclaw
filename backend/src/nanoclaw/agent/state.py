"""Agent state definition using TypedDict."""

from typing import Annotated, Sequence, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """State passed between nodes in the agent graph."""

    messages: Annotated[Sequence[AnyMessage], add_messages]
