"""IAra — Chat page.

Invoca o agente LangGraph de forma sincrona via /chat e exibe
a conversa como interface de chat.
"""

from __future__ import annotations

import uuid

import httpx
import streamlit as st

# ── Session state ─────────────────────────────────────────────────────────────

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages: list[dict[str, str]] = []

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id: str = str(uuid.uuid4())[:8]

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.divider()
    st.caption(f"Conversa: {st.session_state.conversation_id}")
    if st.button("Nova conversa", use_container_width=True, type="primary"):
        st.session_state.chat_messages = []
        st.session_state.conversation_id = str(uuid.uuid4())[:8]
        st.rerun()
    if st.button("Limpar", use_container_width=True):
        st.session_state.chat_messages = []
        st.rerun()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _send(messages: list[dict[str, str]]) -> str:
    base_url   = st.session_state.get("base_url",   "http://localhost:8000")
    tenant_key = st.session_state.get("tenant_key", "test_tenant_001")
    url = f"{base_url.rstrip('/')}/chat/{tenant_key}"
    payload = {
        "conversation_id": st.session_state.conversation_id,
        "messages": messages,
    }
    with httpx.Client(timeout=60) as client:
        r = client.post(url, json=payload)
    r.raise_for_status()
    return r.json().get("reply") or ""

# ── Chat ──────────────────────────────────────────────────────────────────────

st.title("Chat")

for msg in st.session_state.chat_messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

if prompt := st.chat_input("Digite sua mensagem..."):
    st.session_state.chat_messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Aguardando resposta..."):
            try:
                reply = _send(st.session_state.chat_messages)
                st.write(reply)
                st.session_state.chat_messages.append({"role": "assistant", "content": reply})
            except httpx.ConnectError:
                st.error("API indisponivel. Verifique se o servidor esta rodando.")
            except httpx.HTTPStatusError as exc:
                st.error(f"Erro {exc.response.status_code}: {exc.response.text[:200]}")
            except Exception as exc:
                st.error(str(exc))
