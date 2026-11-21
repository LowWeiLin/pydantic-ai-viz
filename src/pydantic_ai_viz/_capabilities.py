"""Which group of the Pydantic AI capabilities docs a capability class belongs to."""

from __future__ import annotations

import re
from typing import Literal, cast, get_args
from urllib.parse import urlsplit, urlunsplit

from ._models import CapabilityCategory

Package = Literal["pydantic_ai", "pydantic_ai_harness"]
_PACKAGES: frozenset[str] = frozenset(get_args(Package))

_GROUPS: dict[CapabilityCategory, tuple[str, ...]] = {
    "harness": ("Coder", "Researcher"),
    "environment": (
        "LocalWorkspace",
        "WindowsWorkspace",
        "FileSystem",
        "SmartSearch",
        "Shell",
        "ModalSandbox",
        "E2BSandbox",
        "KubernetesSandbox",
        "RenderSandbox",
        "SpritesSandbox",
        "DaytonaSandbox",
        "BoxdSandbox",
        "SmolSandbox",
        "BoxLiteSandbox",
        "OpenSandbox",
        "DockerContainer",
        "DockerSandbox",
        "SSHWorkspace",
        "BubblewrappedWorkspace",
    ),
    "tools": ("MCP", "ImageGeneration", "FileUnderstanding", "NativeTool", "Toolset"),
    "integration": (
        "GitHub",
        "Linear",
        "Macroscope",
        "LocalStack",
        "GoogleWorkspace",
        "Notion",
        "Slack",
        "DayAI",
        "Grain",
        "Pylon",
        "StackOne",
        "PostHog",
        "LogfireMCP",
        "Ordinal",
    ),
    "web": (
        "WebSearch",
        "WebFetch",
        "XSearch",
        "Citations",
        "ExaSearch",
        "ExaAgent",
        "YouSearch",
        "YouResearch",
        "KeenableSearch",
        "BrowserUse",
        "PlaywrightBrowser",
    ),
    "reasoning": ("Thinking", "Planning", "SubAgents", "DynamicWorkflow", "Advisor", "BackgroundTools", "Peers"),
    "context": (
        "ToolSearch",
        "Caching",
        "AnthropicCompaction",
        "OpenAICompaction",
        "CodeMode",
        "IPythonCodeMode",
        "ToolBridge",
        "SlidingWindowCompaction",
        "SummarizingCompaction",
        "TieredCompaction",
        "RelevanceCompaction",
        "FallbackCompaction",
        "ClearToolResults",
        "PruneToolResults",
        "ClampOversizedMessages",
        "DeduplicateFileReads",
        "CompactOnRequest",
        "ReportContextUsage",
        "WarnNearLimits",
        "ToolOutputLimits",
        "WarnOnCacheBusts",
        "LabelCrossModelHistory",
    ),
    "knowledge": (
        "Memory",
        "Pixeltable",
        "ConversationSearch",
        "Skills",
        "RepoContext",
        "PydanticAIDocs",
        "PyaiDocs",
        "CurrentTime",
        "ReportStaleFiles",
    ),
    "safety": (
        "HandleDeferredToolCalls",
        "RequestInput",
        "InputGuardrail",
        "OutputGuardrail",
        "StreamGuardrail",
        "ToolGuardrail",
        "Goal",
        "PromptInjectionDefender",
        "SpendLimits",
        "AskUser",
        "SystemReminders",
        "DetectLoops",
        "TrajectoryJudge",
        "RefineOutput",
        "RequireVerification",
        "DenyUnanswered",
        "ReportToolErrors",
        "ToolBudgets",
        "ReportRemainingBudget",
        "WrapUpAtLimits",
        "ToolCallJudge",
        "ApprovalReviewer",
        "Permissions",
    ),
    "self_extension": ("CapabilityCreation",),
    "runtime": (
        "Instrumentation",
        "UseThreadExecutor",
        "BatchModelRequests",
        "PendingMessageDrainCapability",
        "TemporalDurability",
        "DBOSDurability",
        "PrefectDurability",
        "AbsurdDurability",
        "AWSLambdaDurability",
        "AzureDurableTaskDurability",
        "PGTaskDurability",
        "RenderWorkflows",
        "StepPersistence",
        "ManagedPrompt",
        "RepairToolArguments",
        "ModelRouter",
    ),
    "loop": (
        "Hooks",
        "SelectModel",
        "ModelFallback",
        "RetryOnModelErrors",
        "ResolveModelId",
        "PrepareTools",
        "PrepareOutputTools",
        "PrefixTools",
        "IncludeToolReturnSchemas",
        "SetToolMetadata",
        "RaiseContentFilterError",
        "ReinjectSystemPrompt",
        "ProcessHistory",
        "ProcessEventStream",
    ),
}
_CATEGORY: dict[str, CapabilityCategory] = {name: category for category, names in _GROUPS.items() for name in names}


def package_of(dotted: str) -> Package | None:
    """The package a module or a dotted import path is in, when it is Pydantic AI or the harness."""
    head = dotted.partition(".")[0]
    return cast("Package", head) if head in _PACKAGES else None


def category_of(dotted: str, name: str) -> CapabilityCategory | None:
    """The category of class ``name`` defined under ``dotted``; None when it is not one the docs list."""
    return _CATEGORY.get(name) if package_of(dotted) is not None else None


_SECRET = re.compile(
    r"secret|passw|credential|cookie|bearer"  # anywhere in the name
    r"|(token|auth|authorization|headers?|(^|[_-])(api)?key)$"  # at its end: ``access_token``, not ``max_tokens``
    r"|(^|[_-])(env|headers?|authorization)([_-]|$)",  # as a word: ``env``, not ``environment``
    re.IGNORECASE,
)


def is_secret(key: str) -> bool:
    """Whether a parameter of this name may hold a credential, so its value is never shown."""
    return _SECRET.search(key) is not None


def without_credentials(value: str) -> str:
    """A URL without its user info, query and fragment, which may hold a key; any other text unchanged."""
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:  # not a URL after all: an unclosed ``[`` or a port that is not a number
        return value
    if parts.scheme not in ("http", "https", "ws", "wss") or not parts.hostname:
        return value
    netloc = parts.hostname + (f":{port}" if port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
