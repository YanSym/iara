"""IAra — Home page."""

from __future__ import annotations

import streamlit as st

st.title("IAra")

st.markdown("""
IAra é um runtime de backend que conecta o Chatwoot a modelos de linguagem.
Quando um cliente envia uma mensagem no Chatwoot, IAra recebe o webhook, processa
a conversa por um pipeline de orquestração LangGraph, gera uma resposta com o LLM
configurado e envia a resposta de volta pelo Chatwoot.

O sistema foi projetado para implantações multi-tenant, onde cada tenant possui
sua própria configuração, permissões de ferramentas e vinculações de provedor,
com isolamento rigoroso entre tenants.
""")

st.subheader("Como funciona")

st.markdown("""
1. O Chatwoot envia um webhook ao IAra quando uma mensagem de cliente chega.
2. IAra valida o evento contra a configuração do tenant e as regras de eligibilidade.
3. Eventos elegíveis são enfileirados no RabbitMQ para processamento assíncrono.
4. Um worker busca o job, adquire um lease de conversa e executa o pipeline LangGraph.
5. O pipeline passa por eligibilidade, compreensão de mídia, construção de contexto, o agente LLM, guardrails e despacho de comandos.
6. A resposta do agente é gravada em um outbox no Postgres.
7. O drainer de outbox busca e entrega a resposta ao Chatwoot via adaptador MCP.
""")

st.subheader("Tecnologias")

col1, col2 = st.columns(2)

with col1:
    st.markdown("""
**Runtime**
- Python 3.13 com FastAPI e Uvicorn
- LangGraph para orquestração stateful de conversas
- RabbitMQ para enfileiramento assíncrono com dead-letter e retry
- PostgreSQL para idempotência, leases, outbox e auditoria

**Provedores LLM**
- OpenAI (GPT-4o, GPT-4o-mini e modelos da família 5)
- Anthropic (Claude Sonnet e qualquer modelo Claude)
""")

with col2:
    st.markdown("""
**Invariantes de segurança**
- Fail-closed em qualquer ambiguidade de tenant ou capacidade
- Isolamento cross-tenant aplicado antes de cada efeito colateral
- O LLM nunca vê nomes brutos de ferramentas MCP ou o catálogo Chatwoot
- Todas as escritas com efeito colateral passam pelo outbox para entrega efetivamente única
- Sem dados pessoais em logs ou armazenamento durável, apenas hashes e refs

**Status de implementação**
- Fases 0 a 7 completas
- 113 testes automatizados passando
- Fase 8 (catálogo MCP por tenant) aguardando contratação
""")

st.subheader("Teste local")

st.markdown("""
Use a pagina **Chat** para enviar mensagens diretamente ao agente LLM e ver as respostas em tempo real.
O endpoint de chat bypassa o RabbitMQ e invoca o grafo LangGraph de forma sincrona,
o que o torna ideal para iterar sobre o comportamento do agente durante o desenvolvimento.

Use a pagina **Webhooks** para simular payloads de webhook do Chatwoot e verificar
as regras de eligibilidade, incluindo cenarios de aceite e rejeicao.
""")
