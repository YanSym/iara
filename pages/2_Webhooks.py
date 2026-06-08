"""IAra — Webhooks test page.

Simula payloads de webhook do Chatwoot e verifica o resultado de eligibilidade.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import httpx
import streamlit as st

# ── Session state ─────────────────────────────────────────────────────────────

if "webhook_history" not in st.session_state:
    st.session_state.webhook_history: list[dict[str, Any]] = []

# ── Scenarios ─────────────────────────────────────────────────────────────────

SCENARIOS: dict[str, dict[str, Any]] = {
    "Cliente incoming": {
        "content": "Ola, gostaria de agendar uma consulta.",
        "sender_type": "contact",
        "message_type": "incoming",
        "is_private": False,
        "override_account": False,
    },
    "Mensagem saindo": {
        "content": "Claro, vou verificar disponibilidade.",
        "sender_type": "agent_bot",
        "message_type": "outgoing",
        "is_private": False,
        "override_account": False,
    },
    "Bot sender": {
        "content": "Resposta automatica do bot.",
        "sender_type": "agent_bot",
        "message_type": "incoming",
        "is_private": False,
        "override_account": False,
    },
    "Nota privada": {
        "content": "Nota interna: lead qualificado.",
        "sender_type": "user",
        "message_type": "incoming",
        "is_private": True,
        "override_account": False,
    },
    "Cross-tenant": {
        "content": "Tentativa de outro account.",
        "sender_type": "contact",
        "message_type": "incoming",
        "is_private": False,
        "override_account": True,
    },
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_payload(
    account_id: str,
    conversation_id: str,
    content: str,
    sender_type: str,
    message_type: str,
    is_private: bool,
) -> dict[str, Any]:
    return {
        "event": "message_created",
        "id": str(uuid.uuid4()),
        "account": {"id": account_id, "name": "Teste Local"},
        "inbox": {"id": "inbox_001", "channel_type": "Channel::Whatsapp"},
        "conversation": {"id": conversation_id, "status": "open"},
        "message": {
            "id": str(uuid.uuid4()),
            "content": content,
            "message_type": message_type,
            "private": is_private,
            "sender": {"type": sender_type},
        },
    }


def _send_webhook(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    base_url   = st.session_state.get("base_url",   "http://localhost:8000")
    tenant_key = st.session_state.get("tenant_key", "test_tenant_001")
    url = f"{base_url.rstrip('/')}/webhooks/chatwoot/{tenant_key}"
    with httpx.Client(timeout=10) as client:
        r = client.post(url, json=payload, headers={"X-Request-ID": str(uuid.uuid4())})
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}
    return r.status_code, body

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.divider()
    st.text_input("Account ID",        value="11111",    key="account_id")
    st.text_input("Account ID errado", value="99999",    key="wrong_account_id")
    st.text_input("Conversation ID",   value="conv_001", key="conv_id")
    st.divider()
    st.caption("Cenarios rapidos")
    chosen_scenario = st.radio(
        label="Cenario",
        options=list(SCENARIOS.keys()),
        label_visibility="collapsed",
    )
    if st.button("Aplicar cenario", use_container_width=True):
        sc = SCENARIOS[chosen_scenario]
        st.session_state["wh_content"]      = sc["content"]
        st.session_state["wh_sender_type"]  = sc["sender_type"]
        st.session_state["wh_message_type"] = sc["message_type"]
        st.session_state["wh_is_private"]   = sc["is_private"]
        st.session_state["wh_wrong_acct"]   = sc["override_account"]
        st.rerun()
    if st.button("Limpar historico", use_container_width=True):
        st.session_state.webhook_history = []
        st.rerun()

# ── Main ──────────────────────────────────────────────────────────────────────

st.title("Webhooks")

content = st.text_area(
    "Conteudo da mensagem",
    value=st.session_state.get("wh_content", "Ola, gostaria de agendar uma consulta."),
    height=100,
    key="wh_content",
)

col1, col2, col3 = st.columns([2, 2, 1])
with col1:
    sender_options = ["contact", "agent_bot", "user", "system"]
    sender_type = st.selectbox(
        "Remetente",
        sender_options,
        index=sender_options.index(st.session_state.get("wh_sender_type", "contact")),
        key="wh_sender_type",
    )
with col2:
    type_options = ["incoming", "outgoing", "activity", "template"]
    message_type = st.selectbox(
        "Tipo",
        type_options,
        index=type_options.index(st.session_state.get("wh_message_type", "incoming")),
        key="wh_message_type",
    )
with col3:
    is_private = st.checkbox(
        "Nota privada",
        value=st.session_state.get("wh_is_private", False),
        key="wh_is_private",
    )

use_wrong = st.session_state.get("wh_wrong_acct", False)
account_id = st.session_state.get("wrong_account_id", "99999") if use_wrong else st.session_state.get("account_id", "11111")
if use_wrong:
    st.caption(f"Usando account ID errado: {account_id}")

btn_col, _ = st.columns([1, 3])
with btn_col:
    sent = st.button("Enviar", type="primary", use_container_width=True)

# ── Response ──────────────────────────────────────────────────────────────────

if sent:
    payload = _build_payload(
        account_id,
        st.session_state.get("conv_id", "conv_001"),
        content,
        sender_type,
        message_type,
        is_private,
    )

    with st.spinner("Aguardando resposta..."):
        try:
            http_status, body = _send_webhook(payload)
            api_status = body.get("status", "unknown")
        except httpx.ConnectError:
            http_status, body, api_status = 0, {"error": "API indisponivel"}, "error"
        except Exception as exc:
            http_status, body, api_status = 0, {"error": str(exc)}, "error"

    if api_status == "accepted":
        st.success(f"Aceito  {body.get('correlation_id', '')}")
    elif api_status == "rejected":
        st.warning(f"Rejeitado  {body.get('reason', '')}")
    else:
        st.error(f"Erro  HTTP {http_status}")

    with st.expander("JSON completo"):
        st.json(body)

    st.session_state.webhook_history.insert(0, {
        "ts":       datetime.now().strftime("%H:%M:%S"),
        "scenario": chosen_scenario,
        "status":   api_status,
        "reason":   body.get("reason", ""),
        "corr":     body.get("correlation_id", ""),
        "body":     body,
    })

# ── History ───────────────────────────────────────────────────────────────────

if st.session_state.webhook_history:
    st.divider()
    st.subheader("Historico")
    for e in st.session_state.webhook_history[:10]:
        label = e["reason"] or e["corr"] or "sem detalhe"
        with st.expander(f"{e['ts']}  {e['scenario']}  {e['status']}  {label}"):
            st.json(e["body"])
