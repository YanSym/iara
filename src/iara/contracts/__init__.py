"""Pydantic v2 contracts — the canonical data shapes for the IAra runtime.

All cross-module data exchange uses these contracts. Sensitive fields are
marked for redaction so they are stripped from logs, audit events, and
evidence by default.
"""

from iara.contracts.audit import SanitizedEvidence
from iara.contracts.errors import (
    CrossTenantError,
    FailClosedError,
    IaraError,
    PolicyViolationError,
    ProviderError,
    ReadbackFailedError,
)
from iara.contracts.events import (
    CanonicalAttachment,
    CanonicalMessageEvent,
    EligibilityDecision,
    NormalizedChatwootEvent,
    RawEventRef,
)
from iara.contracts.provider import (
    CapabilityResolution,
    OperationalAction,
    ProviderCommand,
    ProviderMutationResult,
    ProviderSecurityContext,
)
from iara.contracts.state import (
    AgentInput,
    AgentOutput,
    ConversationContext,
    ConversationState,
    MediaContext,
    SecurityContext,
)
from iara.contracts.tenancy import TenantContext
from iara.contracts.tools import ToolInvocationRequest, ToolInvocationResult

__all__ = [
    # Events
    "RawEventRef",
    "CanonicalAttachment",
    "CanonicalMessageEvent",
    "NormalizedChatwootEvent",
    "EligibilityDecision",
    # State
    "SecurityContext",
    "ConversationState",
    "MediaContext",
    "ConversationContext",
    "AgentInput",
    "AgentOutput",
    # Tenancy
    "TenantContext",
    # Provider
    "ProviderSecurityContext",
    "CapabilityResolution",
    "ProviderCommand",
    "ProviderMutationResult",
    "OperationalAction",
    # Tools
    "ToolInvocationRequest",
    "ToolInvocationResult",
    # Audit
    "SanitizedEvidence",
    # Errors
    "IaraError",
    "FailClosedError",
    "CrossTenantError",
    "PolicyViolationError",
    "ReadbackFailedError",
    "ProviderError",
]
