# Sprint 22 — Correções (v2.12.1 + v2.13.0) — 2026-06-13

Segunda leva, sobre os 5 pontos reportados após o Sprint 22.

## v2.12.1 (hotfix)

### #1 — 500 ao editar usuário (UNIQUE constraint)
- **Causa:** `users_routes.update` usava `collection.clear()` + `add()`; o flush
  do SQLAlchemy reordenava INSERT antes do DELETE → `UNIQUE constraint failed:
  user_action_permissions`. (Confirmado no journald.)
- **Correção:** bulk-delete (`db.query(...).delete()`, que emite o DELETE na hora)
  + dedupe, para tabelas e ações. Agora é possível editar perfil/ações de
  qualquer usuário a qualquer momento.

### #2 — Dark mode no select de filtro (Select2)
- **Causa:** o CSS dark cobria `.select2-container--default`, mas a página usa o
  tema **`bootstrap-5`** (`.select2-container--bootstrap-5`).
- **Correção:** seletores theme-agnósticos (alta especificidade) cobrindo o tema
  usado — caixa, dropdown, opções e busca no escuro.

### #3 — Remoção dos dizeres de Tolerância/NCM
- Removido o banner `Tolerância R$… NCM crítico` do topo do Auditor (`#engineTol`).
  `engineInfo` continua carregado para uso interno (tolerâncias no detalhe).

### #5 — Data DD/MM/AAAA no modal do documento
- Helper `_fmtProtheusDate` converte `YYYYMMDD` (ex.: 20260612) → `12/06/2026`
  no campo **Emissão** do modal de divergências.

## v2.13.0 — #4: Auditor lista TODOS os documentos

Abordagem escolhida: **registrar todos a cada auditoria** (1 linha por documento).

- **Motor (`auditor.py`):** todo documento auditado é persistido. Sem divergência
  → grava marcador `severity='ok'` (`field_compared='__documento__'`). Novo
  `_doc_key(doc)`: usa a **chave de 44 dígitos** (NFe/CTe) ou sintetiza
  `doc/serie/fornec/loja` (NF avulsa/sem chave).
- **`internal_audit.py`:** novo `doc_filter` permite localizar o documento por
  doc+série+fornec+loja (além da chave).
- **`document-audit`:** o "Detalhes" aceita a chave (44 díg.) **ou** o doc_key
  sintético, reabrindo documentos sem chave (antes davam 422).
- **`grouped-anomalies`:** `qtd_divergencias` conta apenas `severity != 'ok'`;
  documentos limpos aparecem com badge **"OK"** verde quando o toggle "Exibir
  apenas divergências" está **desligado**.
- **KPIs/Dashboard (`summary`, histograma, fiscal-recent):** todos passam a
  **ignorar** os marcadores `ok` (não inflam ANOMALIAS/CRÍTICAS/AVISOS).

> ⚠️ **Requer nova auditoria + validação com dado real.** Os documentos OK só
> aparecem para execuções FEITAS a partir desta versão (registros antigos não
> têm marcador). Rode uma **Nova auditoria** e:
> 1. Deixe o toggle **"Exibir apenas divergências" DESLIGADO** para ver todos.
> 2. Não filtre `doc_models` (ou inclua 57) para trazer CTe junto com NFe(55).
> 3. Abra o "Detalhes" de um documento OK e de um sem chave para conferir.
>
> CTe só aparece se o ERP internaliza o CTe na SDS/SDT como os NFe — confirmar
> com dado real.
