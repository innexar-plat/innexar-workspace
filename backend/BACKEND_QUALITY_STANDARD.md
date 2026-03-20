# Backend Quality Standard (Innexar Workspace)

Este documento padroniza o backend para manter operacao estavel e evolucao continua.

## Stack e Organizacao

- Runtime principal: `backend/build/lib/app`
- API em 3 camadas:
  - `/api/public` (publico)
  - `/api/portal` (cliente autenticado)
  - `/api/workspace` (staff)
- Modulos principais: billing, checkout, products, crm, projects, support, files, notifications, dashboard, hestia, system.

## Regras de Padrao

- Sempre usar `org_id` para isolamento de dados quando o modelo suportar multi-org.
- Endpoints publicos devem ter validacao de entrada e rate limit quando aceitarem formularios.
- Toda acao critica deve gerar `audit log`.
- Novos endpoints devem ter contrato de request/response com Pydantic.
- Evitar logica de negocio no router quando possivel; mover para service quando crescer.

## CRM: melhorias implementadas

- Intake publico de lead (`/api/public/web-to-lead` e alias `/api/public/contact/submit`):
  - normalizacao de email/telefone
  - deduplicacao de contato por `org_id + email`
  - score inicial automatico do lead
  - status inicial: `qualificado` quando score >= 55
  - criacao de atividade com mensagem recebida
  - audit payload enriquecido
- Workspace CRM:
  - busca por texto em contatos (`q`)
  - busca por texto em leads (`search`)
  - endpoint resumo operacional: `GET /api/workspace/crm/summary`
  - vinculo automatico com Customers por email (`contact.customer_id` quando existir cliente)

## Status por modulo

- billing:
  - status: bom
  - cobertura: workspace + portal + webhooks publicos
  - gap: ampliar testes de cobranca de borda (falha de gateway e retries)
- checkout:
  - status: bom
  - cobertura: endpoint publico unico de inicio de checkout
  - gap: testes e2e com cenarios de cancelamento/erro de pagamento
- products:
  - status: bom
  - cobertura: catalogo publico com planos
  - gap: validacoes adicionais de consistencia de planos ativos
- crm:
  - status: melhorado
  - cobertura: CRUD workspace + intake publico + summary
  - gap: service dedicado para scoring e enriquecimento por UTM/campanha
- customers:
  - status: bom
  - cobertura: CRUD workspace + relacionamento com CRM/Projects/Support
  - gap: reconciliacao automatica de dados de contato entre modulos
- projects:
  - status: bom
  - cobertura: workspace + portal
  - gap: templates de fluxo e automacoes de transicao de status
- support:
  - status: bom
  - cobertura: workspace + portal com tickets e mensagens
  - gap: SLA automatizado e escalonamento por prioridade
- files:
  - status: bom
  - cobertura: workspace + portal para arquivos de projeto
  - gap: politicas de retencao e auditoria de download/upload
- notifications:
  - status: bom
  - cobertura: portal notifications
  - gap: centralizacao de eventos multi-modulo
- dashboard:
  - status: parcial
  - cobertura: endpoints workspace
  - gap: consolidar KPIs de CRM+billing+support em uma visao unica
- orders:
  - status: bom
  - cobertura: workspace orders
  - gap: trilha de auditoria de mudancas de status de pedido
- hestia:
  - status: bom
  - cobertura: workspace para operacao de hosting/provisioning
  - gap: testes de resiliencia com falhas de API externa
- system:
  - status: bom
  - cobertura: configuracoes, seeds e integracoes
  - gap: hardening de governanca de seeds em ambiente de producao

## Checklist de Saude

Executar periodicamente:

```bash
cd backend
/opt/Innexar-Brasil/.venv/bin/python -m ruff check build/lib/app tests
/opt/Innexar-Brasil/.venv/bin/python -m pytest -q
```

## Riscos conhecidos

- O repositorio trabalha com codigo runtime em `build/lib`; manter disciplina de commit para nao perder alteracoes.
- Historicamente faltavam arquivos-fonte de testes (somente cache). Foi iniciado baseline de testes unitarios de contrato de rota.

## Proximos passos recomendados (para 10/10)

1. Expandir testes de integracao com DB para CRM (criacao de lead, deduplicacao, score, summary).
2. Adicionar migracoes Alembic versionadas no repositorio (hoje a pasta de versions nao esta populada).
3. Mover regras de scoring para um service dedicado com configuracao por feature flag.
4. Criar dashboard de SLO para API publica (429 rate, 5xx, tempo de resposta).
