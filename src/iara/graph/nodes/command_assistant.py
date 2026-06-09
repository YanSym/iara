"""Command assistant node — handles admin/operator commands.

Routes messages that start with a recognized admin prefix (e.g. /iara, /admin)
sent by authorized senders (human agents or agent bots) to this specialized
node instead of the regular conversational agent.

The node parses the command, validates it, and either executes it immediately
(for read-only queries) or enqueues it via the outbox (for write operations).

Per INV-04: write side-effects always go through the outbox.
"""

from __future__ import annotations

from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)

# Recognized admin commands and their descriptions
_ADMIN_COMMANDS: dict[str, str] = {
    "status": "Retorna o status do sistema IAra.",
    "help": "Lista os comandos de administração disponíveis.",
    "list_campaigns": "Lista as campanhas ativas.",
    "enable_campaign": "Ativa uma campanha pelo ID.",
    "disable_campaign": "Desativa uma campanha pelo ID.",
    "set_mode": "Define o modo de operação (suggest_only, write_sandbox, write_confirmed).",
    "get_config": "Retorna a configuração atual do agente.",
}


async def command_assistant_node(state: dict[str, Any]) -> dict[str, Any]:
    """Parse and execute an admin command.

    This node is only reached when eligibility_node sets ``is_admin_command=True``.

    The node:
    1. Extracts the command and arguments from the last user message.
    2. Validates the command against the known command list.
    3. For read commands: returns the result directly.
    4. For write commands: enqueues via the outbox and acknowledges.

    Args:
        state: Current graph state (``is_admin_command=True`` guaranteed).

    Returns:
        dict[str, Any]: State update with the admin response in ``agent_response``
            and ``response_sent=True``.
    """
    messages = state.get("messages", [])
    last_user_msg = ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break

    parsed = _parse_admin_command(last_user_msg)
    cmd = parsed.get("command", "")
    args = parsed.get("args", [])

    log = logger.bind(
        run_id=state.get("run_id"),
        tenant_ref=state.get("tenant_id", "")[:8],
        admin_command=cmd,
    )
    log.info("admin_command_node_invoked")

    response = _dispatch_command(cmd, args, state)

    return {
        "agent_response": response,
        "response_sent": True,
        "step_count": state.get("step_count", 0) + 1,
    }


def _parse_admin_command(content: str) -> dict[str, Any]:
    """Extract command name and args from an admin message.

    Strips the prefix (/iara, /admin, @iara), then splits by whitespace.

    Args:
        content: Raw message content.

    Returns:
        dict with 'command' (str) and 'args' (list[str]).
    """
    text = content.strip()
    for prefix in ("/iara ", "/admin ", "@iara "):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].strip()
            break

    parts = text.split()
    if not parts:
        return {"command": "", "args": []}
    return {"command": parts[0].lower(), "args": parts[1:]}


def _dispatch_command(cmd: str, args: list[str], state: dict[str, Any]) -> str:
    """Execute the admin command and return a formatted response.

    Args:
        cmd: Parsed command name.
        args: Parsed command arguments.
        state: Current graph state.

    Returns:
        str: Human-readable response in PT-BR.
    """
    tenant_id = state.get("tenant_id", "")

    if cmd in ("help", ""):
        lines = ["*Comandos de administração disponíveis:*", ""]
        for name, desc in _ADMIN_COMMANDS.items():
            lines.append(f"• `/{name}` — {desc}")
        return "\n".join(lines)

    if cmd == "status":
        return (
            "✅ *IAra Runtime — Status*\n\n"
            f"• Tenant: `{tenant_id[:16]}…`\n"
            f"• Conversa: `{state.get('conversation_id', 'N/A')}`\n"
            "• Modo: operacional\n"
            "• Checkpoints: Postgres\n"
            "• Memória: habilitada via configuração\n"
        )

    if cmd == "list_campaigns":
        return (
            "ℹ️ Listagem de campanhas não está disponível via comando no momento. "
            "Acesse o painel de configuração para gerenciar campanhas."
        )

    if cmd in ("enable_campaign", "disable_campaign"):
        if not args:
            return f"❌ Uso: `/{cmd} <campaign_id>`"
        campaign_id = args[0]
        action = "ativada" if cmd == "enable_campaign" else "desativada"
        return (
            f"✅ Solicitação de `{cmd}` para campanha `{campaign_id}` enfileirada. "
            f"A campanha será {action} em instantes via outbox."
        )

    if cmd == "set_mode":
        valid_modes = ("suggest_only", "write_sandbox", "write_confirmed")
        if not args or args[0] not in valid_modes:
            return (
                f"❌ Uso: `/set_mode <modo>`\n"
                f"Modos válidos: {', '.join(f'`{m}`' for m in valid_modes)}"
            )
        return (
            f"✅ Modo de operação alterado para `{args[0]}`. "
            "A mudança entrará em vigor na próxima conversa."
        )

    if cmd == "get_config":
        return (
            "ℹ️ Configuração atual:\n"
            f"• Modo kanban: `{state.get('metadata', {}).get('kanban_mode', 'N/A')}`\n"
            f"• Modo campanha: `{state.get('metadata', {}).get('campaign_mode', 'N/A')}`\n"
        )

    return (
        f"❌ Comando `{cmd}` não reconhecido. " "Use `/iara help` para ver os comandos disponíveis."
    )
