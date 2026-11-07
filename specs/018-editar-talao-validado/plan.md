# Implementation Plan: Editar um talão já validado

**Branch**: `feature/editar-talao-validado` | **Date**: 2026-11-07 | **Spec**: [spec.md](./spec.md)

## Summary

Uma ação nova, `reopen`, que leva um talão `CONFIRMED` de volta à revisão. A edição e a nova confirmação são as que já existem (`PATCH /v1/receipts/{id}`, `POST /v1/receipts/{id}/confirm`).

## Technical Context

- **Código**: `services/receipts.reopen` (versão, estado, avaliação, auditoria `RECEIPT_REOPENED`) e `POST /v1/receipts/{id}/reopen` em `api/routers/receipts.py`. O controlo da versão sai de `_check_editable` para `_check_version`, partilhado.
- **Esquema**: nenhuma migração. `confirmed_at` e `confirmed_by` voltam a `NULL`.
- **Contrato**: `openapi.json` regenerado (`make openapi`).

## Constitution Check

- VII (simplicidade): sem estado novo nem histórico de versões; reaberto é um talão em revisão como os outros.
