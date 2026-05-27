from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Set


class OGCodeBlock(BaseModel):
    lang: str
    content: str


class OGNodeMeta(BaseModel):
    """Index-level routing metadata for a single node (Pass 1)."""
    node_id: str
    node_type: str
    scope: str = "all"
    confidence: float = 1.0
    keywords: List[str] = Field(default_factory=list)
    skip_if_known: bool = False


class OGIndex(BaseModel):
    """The routing table of a .og file. Read during Pass 1."""
    nodes: Dict[str, OGNodeMeta] = Field(default_factory=dict)

    def to_llm_context(self, agent_role: str = "all") -> str:
        """
        Formats the routing table into a dense, token-optimized string for LLM context windows.
        Automatically filters out nodes the agent role is not allowed to see.
        """
        lines = []
        for node_id, meta in self.nodes.items():
            if meta.scope not in ("all", agent_role) and agent_role != "all":
                continue
            
            # Keep keywords concise to save tokens
            kws = ", ".join(meta.keywords)
            
            # Optional attributes
            skip_marker = " [skip_if_known]" if meta.skip_if_known else ""
            
            # Format: - node_id (type): keyword1, keyword2
            lines.append(f"- {node_id} ({meta.node_type}){skip_marker}: {kws}")
            
        if not lines:
            return "No accessible nodes found."
            
        return "AVAILABLE SKILL NODES:\n" + "\n".join(lines)


class OGEdge(BaseModel):
    """A typed, directed relationship between two nodes."""
    direction: str         # "out" (->) | "in" (<-) | "bidi" (<>)
    edge_type: str         # "precedes", "requires", "see-also", etc.
    target: str            # target node ID
    condition: Optional[str] = None


class OGAssertion(BaseModel):
    """Parsed ::assertion block — validation logic embedded in the file."""
    trigger: str = ""
    checks: List[str] = Field(default_factory=list)
    on_pass: List[str] = Field(default_factory=list)
    on_fail: List[str] = Field(default_factory=list)
    max_retries: int = 0


class OGNodeExtraction(BaseModel):
    """Raw content extracted from a single node by the batch extractor."""
    dense: str = ""
    full_blocks: List[str] = Field(default_factory=list)
    code_blocks: List[OGCodeBlock] = Field(default_factory=list)
    tables: List[str] = Field(default_factory=list)
    steps: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    edges_raw: List[str] = Field(default_factory=list)
    assertion_raw: List[str] = Field(default_factory=list)


class OGQueryResult(BaseModel):
    """Final result returned to the agent after a query."""
    node_id: str
    node_type: str
    dense: str
    full: Optional[str] = None
    code_blocks: List[OGCodeBlock] = Field(default_factory=list)
    tables: List[str] = Field(default_factory=list)
    steps: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    edges: List[OGEdge] = Field(default_factory=list)
    assertion: Optional[OGAssertion] = None
    confidence: float = 1.0
    pass_level: int = 2


class OGChangelogEntry(BaseModel):
    """A single row from the ::changelog block."""
    date: str
    action: str       # "added" | "updated" | "deprecated"
    node_id: str
    description: str


class OGDeltaResult(BaseModel):
    """Structured delta: which nodes were added, updated, or deprecated."""
    added: List[str] = Field(default_factory=list)
    updated: List[str] = Field(default_factory=list)
    deprecated: List[str] = Field(default_factory=list)
    entries: List[OGChangelogEntry] = Field(default_factory=list)


class OGSession(BaseModel):
    """Tracks visited nodes and cached indices across queries."""
    visited: Set[str] = Field(default_factory=set)
    cached_indices: Dict[str, dict] = Field(default_factory=dict)
    cached_versions: Dict[str, str] = Field(default_factory=dict)


class OGNode(BaseModel):
    """Full structural node (used by the transpiler, not the reader)."""
    node_id: str
    node_type: str
    content_blocks: List[Dict[str, str]] = Field(default_factory=list)
    edges: List[str] = Field(default_factory=list)
    confidence: float = 0.95
    scope: str = "all"
    updated: str = ""
    dense: str = ""
    index_keywords: str = ""
