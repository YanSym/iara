"""Command authorization — governs which senders may issue admin commands.

Admin commands are operator-level operations (e.g. triggering campaigns,
switching bot mode, reconfiguring routing) sent from inside Chatwoot by a
human agent or agent bot. They are distinct from lead/customer messages.

CommandRequesterBinding maps (tenant_id, sender_type, optional sender_ref)
to a set of allowed command categories.

This is an in-memory implementation. In Phase 8 it will be backed by the
command_requester_bindings DB table.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from iara.observability.logging import get_logger

logger = get_logger(__name__)

# Sender types that can issue admin commands by default.
# Human agents inside Chatwoot use "agent"; automated systems use "agent_bot".
_DEFAULT_AUTHORIZED_SENDER_TYPES = frozenset({"agent", "agent_bot"})

# Admin command prefixes — a message must start with one of these tokens
# (case-insensitive) for the admin routing path to activate.
_ADMIN_PREFIXES = ("/iara ", "/admin ", "@iara ")

# Message content length below which we never route as admin command
# (single-word message like "/iara" without a payload is just a typo).
_MIN_ADMIN_MESSAGE_LENGTH = 8


@dataclass(frozen=True)
class AuthorizationResult:
    """Result of an authorization check.

    Attributes:
        allowed: Whether the action is authorized.
        reason: Human-readable explanation (for logs, never exposed to users).
        is_admin_command: True when the message is recognized as an admin command.
    """

    allowed: bool
    reason: str
    is_admin_command: bool = False


@dataclass
class CommandRequesterBinding:
    """Binding that declares which senders are authorized for admin commands.

    Args:
        tenant_id: The tenant UUID string.
        authorized_sender_types: Sender types allowed to issue admin commands.
        authorized_sender_refs: Optional explicit sender IDs (empty = all of type).
    """

    tenant_id: str
    authorized_sender_types: frozenset[str] = field(
        default_factory=lambda: _DEFAULT_AUTHORIZED_SENDER_TYPES
    )
    authorized_sender_refs: frozenset[str] = field(default_factory=frozenset)


class CommandAuthorizationGuard:
    """Checks whether an incoming message should be routed as an admin command.

    Uses ``CommandRequesterBinding`` (currently in-memory; Phase 8 adds DB
    backing) to decide if the sender is authorized.

    Args:
        bindings: Mapping of tenant_id → CommandRequesterBinding.
    """

    def __init__(
        self,
        bindings: dict[str, CommandRequesterBinding] | None = None,
    ) -> None:
        self._bindings = bindings or {}

    def register(self, binding: CommandRequesterBinding) -> None:
        """Register or update an authorization binding for a tenant.

        Args:
            binding: The binding to register.
        """
        self._bindings[binding.tenant_id] = binding

    def check(
        self,
        tenant_id: str,
        sender_type: str,
        sender_ref: str,
        message_content: str,
    ) -> AuthorizationResult:
        """Determine whether this message is an authorized admin command.

        The check has two layers:
        1. Is the message content an admin command? (prefix match)
        2. Is the sender authorized to issue admin commands?

        Args:
            tenant_id: The tenant UUID string.
            sender_type: Chatwoot sender type (e.g. "contact", "agent", "agent_bot").
            sender_ref: Sender identifier (e.g. agent ID or bot name).
            message_content: The message text.

        Returns:
            AuthorizationResult: Result of the check.
        """
        content = (message_content or "").strip()
        is_admin_prefix = (
            any(content.lower().startswith(pfx) for pfx in _ADMIN_PREFIXES)
            and len(content) >= _MIN_ADMIN_MESSAGE_LENGTH
        )

        if not is_admin_prefix:
            return AuthorizationResult(
                allowed=True,
                reason="not_admin_command",
                is_admin_command=False,
            )

        # Message looks like an admin command — check sender authorization
        binding = self._bindings.get(tenant_id) or CommandRequesterBinding(tenant_id=tenant_id)

        type_ok = sender_type in binding.authorized_sender_types
        ref_ok = not binding.authorized_sender_refs or sender_ref in binding.authorized_sender_refs

        if type_ok and ref_ok:
            logger.info(
                "admin_command_authorized",
                tenant_ref=tenant_id[:8],
                sender_type=sender_type,
                sender_ref_prefix=sender_ref[:8] if sender_ref else "none",
            )
            return AuthorizationResult(
                allowed=True,
                reason="admin_authorized",
                is_admin_command=True,
            )

        logger.warning(
            "admin_command_denied",
            tenant_ref=tenant_id[:8],
            sender_type=sender_type,
            reason="unauthorized_sender",
        )
        return AuthorizationResult(
            allowed=False,
            reason="unauthorized_sender",
            is_admin_command=True,
        )
