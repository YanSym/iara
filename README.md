# IAra — Runtime de Agente Conversacional Multi-Tenant

IAra é um runtime de agente conversacional seguro e pronto para produção, projetado para implantações SaaS multi-tenant. Ele se conecta ao [Chatwoot](https://www.chatwoot.com/) como frontend de mensagens, usa LangGraph para orquestrar um agente baseado em LLM e aplica sete invariantes de segurança inegociáveis em toda a pilha de chamadas.

## O que faz

```
Contato envia mensagem no Chatwoot
          │
          ▼
  [API de Webhook IAra]  --- validação de tenant --- normalização de evento --- verificação de eligibilidade
          │ aceito
          ▼
  [Fila RabbitMQ]
          │
          ▼
  [Worker IAra]  --- agente LangGraph --- 20 ferramentas governadas --- enfileiramento no outbox
          │
          ▼
  [Drainer de Outbox]  --- adaptador Chatwoot MCP --- confirmação de readback
          │
          ▼
  Resposta do agente entregue ao contato
```

## Funcionalidades

- **Multi-tenant** — cada operação é isolada e reverificada por tenant antes de qualquer efeito colateral
- **Segurança fail-closed** — 7 invariantes aplicados; qualquer ambiguidade lança `FailClosedError`, nunca um fallback permissivo
- **Orquestração LangGraph** — grafo stateful com 7 nós, roteamento condicional e memória de conversa
- **20 ferramentas de agente governadas** — agendamento, kanban, campanhas, follow-up, KB, voz, lead, histórico
- **Padrão outbox** — todos os efeitos colaterais são enfileirados no Postgres e drenados de forma assíncrona (entrega efetivamente única)
- **Redação compatível com LGPD** — nenhum dado pessoal, segredo ou payload bruto em logs ou armazenamento durável
- **Suporte a dois provedores LLM** — Anthropic Claude ou OpenAI, configurável por implantação

## Início Rápido

```bash
# Requisitos: Python 3.13, uv, Docker

# 1. Instalar dependências
uv sync --all-groups

# 2. Configurar o ambiente
cp .env.example .env
# Edite o .env — no mínimo defina OPENAI_API_KEY ou ANTHROPIC_API_KEY

# 3. Subir a infraestrutura (Postgres + RabbitMQ)
make up

# 4. Aplicar o schema do banco
make migrate

# 5. Iniciar a API (Terminal 1)
make run

# 6. Iniciar o worker (Terminal 2)
make worker

# 7. (Opcional) Abrir a UI de teste local
make ui    # http://localhost:8501
```

A API estará disponível em `http://localhost:8000`. Swagger UI em `http://localhost:8000/docs`.

## Comandos de Desenvolvimento

```bash
make format        # black + ruff --fix + flake8 (corrige e valida)
make lint          # ruff + flake8 (somente leitura)
make type          # mypy strict (0 erros)
make test-unit     # 110 testes sem serviços externos (unit + security), ~3 s
make test-security # 35 testes de invariantes de segurança (INV-01 a INV-05)
make test          # todos os 113 testes
make check         # format + lint + type + test-unit (gate de CI)
```

## Invariantes de Segurança

Esses invariantes são aplicados no código e verificados por testes automatizados. Não podem ser desabilitados.

| Num | Nome | Regra | Arquivo de teste |
|-----|------|-------|-----------------|
| INV-01 | Fail-Closed | Qualquer ambiguidade lança `FailClosedError` antes de qualquer chamada externa | `tests/security/test_fail_closed.py` |
| INV-02 | Sem Cross-Tenant | Conta do provider reverificada antes de cada efeito colateral | `tests/security/test_cross_tenant.py` |
| INV-03 | LLM Isolado do MCP Raw | O agente vê apenas nomes lógicos de ferramentas, nunca nomes brutos da API Chatwoot | Separação `AgentToolRegistry` e `ChatwootMcpRegistry` |
| INV-04 | Efeitos Colaterais Efetivamente Únicos | Todas as escritas passam pelo outbox — sem mutações diretas dentro do grafo | `OutboxDrainerWorker` |
| INV-05 | Sem PII em Armazenamento Durável | Apenas hashes, refs e contagens em storage e logs | `tests/security/test_redaction.py` |
| INV-06 | Escritas de Alto Risco são Controladas | Campanhas padrão em `draft_only`, kanban em `suggest_only` | `ToolPolicyGuard` |
| INV-07 | Produção Bloqueada | `IARA_PRODUCTION_AUTHORIZED=true` obrigatório para qualquer caminho de produção | `Settings.is_production` |

Especificação completa: [`docs/INVARIANTS.md`](docs/INVARIANTS.md)

## Configuração de LLM

IAra suporta dois provedores, selecionáveis via `LLM_PROVIDER`:

| Provedor | Variável da chave | Variável do modelo | Observação |
|----------|-------------------|--------------------|------------|
| `anthropic` (padrão) | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` | Padrão: `claude-sonnet-4-6` |
| `openai` | `OPENAI_API_KEY` | `OPENAI_MODEL` | Padrão: `gpt-4o` |

**Detecção automática de família OpenAI** — baseada no nome do modelo:
- Nome contém `4` (ex: `gpt-4o`, `gpt-4o-mini`, `o4-mini`) → `temperature=0`
- Nome contém `5` (ex: `gpt-5`, `o5`) → `reasoning_effort=low`

## Estrutura do Projeto

```
mcp_platform/
├── src/iara/
│   ├── api/                    # App FastAPI + routers
│   │   ├── app.py              # Factory da aplicação, lifespan (init RabbitMQ)
│   │   └── routers/
│   │       ├── webhooks.py     # POST /webhooks/chatwoot/{tenant_key}
│   │       ├── chat.py         # POST /chat/{tenant_key} (invocação síncrona para dev)
│   │       └── admin.py        # Endpoints de health e sandbox
│   ├── config/
│   │   └── settings.py         # pydantic-settings, todas as variáveis, enum LlmProvider
│   ├── config_publishing/      # Publicação de configuração de tenant
│   ├── contracts/              # Modelos de domínio Pydantic v2
│   ├── eligibility/            # Normalizador + verificador de eligibilidade (7 regras)
│   ├── graph/                  # Orquestração LangGraph
│   │   ├── builder.py          # build_conversational_graph() + build_production_graph()
│   │   ├── edges.py            # Funções de borda condicional
│   │   ├── state.py            # GraphState TypedDict (total=False, reducer messages)
│   │   └── nodes/              # eligibility, media_understanding, context_builder
│   │                           # agent, tool_executor, guardrails, command_dispatch
│   ├── llm/
│   │   └── factory.py          # build_llm() — Anthropic ou OpenAI com detecção de família
│   ├── media/                  # Subgrafo de compreensão de mídia (áudio, imagem, doc)
│   ├── memory/                 # Store de memória de conversa (MemorySaver)
│   ├── messaging/              # Topologia RabbitMQ, publisher, consumer
│   ├── persistence/            # ORM assíncrono SQLAlchemy 2.0 + 4 repositórios
│   ├── provider/               # Protocolo ProviderAdapter + adaptador Chatwoot MCP
│   ├── security/               # redact_dict(), RedactionProcessor, guards fail-closed
│   ├── tenancy/                # TenantResolver com cache TTL
│   ├── tools/                  # 20 ferramentas de agente
│   │   ├── registry.py         # AgentToolRegistry
│   │   ├── gateway.py          # AgentToolMcpGateway
│   │   ├── policy_guard.py     # ToolPolicyGuard
│   │   ├── executor.py         # ToolExecutor (leitura / rascunho / outbox)
│   │   ├── skill_resolver.py   # Resolução de skill por tenant
│   │   └── catalog/            # agendamento, qualificação, kanban, campanhas,
│   │                           # follow-up, kb, voz, lead, histórico
│   └── workers/
│       ├── main.py             # Entrypoint do worker (inicia ambas as tarefas)
│       ├── job_consumer.py     # Consumer RabbitMQ → runner LangGraph
│       └── outbox_drainer.py   # Outbox Postgres → adaptador provider → readback
│
├── tests/
│   ├── unit/                   # 75 testes unitários — sem serviços externos
│   ├── security/               # 35 testes de invariantes INV-01 a INV-05 (também marcados unit)
│   └── integration/            # 3 stubs — testcontainers ainda não conectados
│
├── migrations/                 # Migrations Alembic assíncronas
├── docs/                       # INVARIANTS, arquitetura, configuração, runbook
├── scripts/init_db.sql         # Bootstrap do Postgres
├── ui.py                       # UI de teste local Streamlit (página Home)
├── pages/
│   ├── 1_Chat.py               # Página de chat com o agente
│   └── 2_Webhooks.py           # Página de teste de webhooks
├── Dockerfile                  # Imagem Python 3.13 multi-stage
├── docker-compose.yml          # Postgres + RabbitMQ + api + worker (profiles)
├── Makefile                    # Todos os comandos de desenvolvimento
├── pyproject.toml              # Python 3.13, config de black/ruff/mypy/pytest
└── .env.example                # Todas as variáveis com valores padrão seguros
```

## Arquitetura

```
[Chatwoot] -- webhook --> [FastAPI /webhooks/{tenant_key}]
                                │
                      TenantResolver (fail-closed, cache TTL)
                                │
                      ChatwootEventNormalizer
                        (remove PII, produz apenas hash-ref)
                                │
                      EligibilityChecker (7 regras)
                                │ aceito
                      [RabbitMQ: iara.jobs.conversation]
                                │
                      [Worker: JobConsumerWorker]
                                │
                      LeaseRepository.acquire()
                                │
                      [Grafo LangGraph]
                        eligibility --> media_understanding
                        --> context_builder --> agent
                        agent <--> tool_executor (loop)
                        --> guardrails --> command_dispatch
                                │
                      [Postgres: provider_command_outbox]
                                │
                      [Worker: OutboxDrainerWorker]
                                │
                      [ChatwootMcpAdapter] --> [API Chatwoot]
                                │
                      ReadbackService.confirm()
```

### Duas Camadas MCP

O LLM **nunca vê** os nomes brutos das ferramentas MCP do Chatwoot (INV-03):

| Camada | Usada por | Conteúdo exposto |
|--------|-----------|-----------------|
| Agent Tools (`AgentToolRegistry`) | LLM / nó agent do LangGraph | Nomes lógicos de negócio: `schedule_appointment`, `kanban_update`... |
| Chatwoot MCP operacional (`ChatwootMcpRegistry`) | Somente o Outbox Drainer | Nomes brutos das ferramentas da API Chatwoot |

## Variáveis de Ambiente

Veja `.env.example` para referência completa com descrições e valores padrão seguros. Variáveis críticas:

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `IARA_ENV` | `development` | `development`, `sandbox`, `staging` ou `production` |
| `LLM_PROVIDER` | `anthropic` | `anthropic` ou `openai` |
| `OPENAI_API_KEY` | | Chave direta (somente dev); use `OPENAI_API_KEY_REF` em produção |
| `ANTHROPIC_API_KEY` | | Chave direta (somente dev); use `ANTHROPIC_API_KEY_REF` em produção |
| `DATABASE_URL` | `postgresql+asyncpg://iara:iara_dev@localhost:5432/iara_dev` | URL SQLAlchemy assíncrona |
| `RABBITMQ_URL` | `amqp://iara:iara_dev@localhost:5672/iara` | URL de conexão AMQP |
| `IARA_PRODUCTION_AUTHORIZED` | `false` | Deve ser `true` para qualquer caminho de produção |
| `IARA_KANBAN_DEFAULT_MODE` | `suggest_only` | `suggest_only`, `write_sandbox` ou `write_confirmed` |
| `IARA_CAMPAIGN_DEFAULT_MODE` | `draft_only` | `draft_only`, `dry_run`, `sandbox` ou `approved_send` |

## Observabilidade

| Interface | URL | Credenciais |
|-----------|-----|-------------|
| FastAPI Swagger | `http://localhost:8000/docs` | somente em dev |
| RabbitMQ Management | `http://localhost:15672` | `iara` / `iara_dev` |

Todos os logs são JSON estruturado (via `structlog`) e passam pelo `RedactionProcessor` antes da emissão. Nenhum dado pessoal, token ou payload bruto aparece nos logs.

## Licença

Proprietário — Digi2B. Todos os direitos reservados.
