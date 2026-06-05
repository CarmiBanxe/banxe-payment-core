Canon version: 1.3
Date: 2026-05-23

# Factory Modules — Operational Definitions

This document is the canonical operational definition source for optional modules listed in CANON.md v1.3 §11.
Without these definitions, module names are not part of the canon (CANON.md v1.3 §6).

## Common contract for every module

- A module is an optional capability profile, not a permanent extension to the axiom.
- A module may only strengthen the Decision-Making Axiom, never weaken it.
- Loading, unloading, and updating a module must be reported explicitly.
- Forbidden modules (legal, legalfr, legaleu, legalqa, ultralegal, academic-legal, medical) must be refused.
- Each module declares: scope, allowed-tools, prohibitions, enforcement level.

## Format for each module

- Scope: what the module is for.
- Allowed tools: which actions/tools the agent may use while the module is active.
- Prohibitions: what is forbidden while the module is active.
- Enforcement level: L0/L1/L2/L3 per CANON.md v1.3 §3.

---

## technical

- Scope: general software engineering work — code, configs, CI, infrastructure-as-code, repos, tooling. Default factory module.
- Allowed tools: Read, Grep, Glob, Bash (non-destructive by default), Write/Edit only on non-canonical files, code execution under `!implementation`.
- Prohibitions:
  - no direct edits to L0–L2 canon files outside the canon change procedure;
  - no destructive shell operations without explicit user confirmation (CANON.md v1.2 §8 Plan-Risk-Block);
  - no introduction of a new source of truth outside factory canon.
- Enforcement level: L1 (CI runs canon-guardian on every PR; technical module is the baseline assumed by quality-core workflows).

## financial

- Scope: financial logic, money handling, transaction flows, settlement, payouts, balances.
- Allowed tools: Read, Grep, Glob, Bash, Write/Edit on non-canonical financial-logic files.
- Prohibitions:
  - no float for money — only Decimal/equivalent;
  - no edits to live billing config, secrets, or compliance-sensitive paths;
  - no removal of concurrency guards on money-touching workflows (PATTERNS.md §3.3);
  - no display of monetary values without tabular-num formatting and disclosure header where required;
  - no auto-fix on findings of money-safety reviewer (CANON.md v1.2 §5 QC cascade).
- Enforcement level: L2 (money-safety guards must be required checks in any repo where this module is loaded).

## scientific

- Scope: data analysis, modeling, statistical reasoning, research-style claims.
- Allowed tools: Read, Grep, Glob, Bash for analysis runs, Write/Edit on analysis artifacts and reports.
- Prohibitions:
  - no factual claim without [FACT] / [ВЫВОД] / [НЕИЗВЕСТНО] marker (CANON.md v1.2 §2 Epistemic Accuracy Guard);
  - no auto-verify without explicit user permission and `!strict`;
  - no presentation of correlation as causation;
  - no suppression of negative results to favor positive narrative.
- Enforcement level: L1 (Epistemic Accuracy Guard runs on every output produced under this module).

## creative

- Scope: design proposals, copy, content, UX direction, visual concepts (text-form only).
- Allowed tools: Read, Grep, Glob, Write/Edit on non-canonical design/content files.
- Prohibitions:
  - no edits to canonical UI generation artifacts (CANON.md v1.2 §9 No-Silent-Rewrite);
  - no claim of factual basis for purely creative assertions;
  - no override of token/parity/typecheck discipline of ui-sync-core;
  - no production of binary or external-image artifacts (text/ASCII only).
- Enforcement level: L1 (ui-sync-core boundaries must hold; advisory-only research layers per PATTERNS.md §5.1 remain advisory).

## educational

- Scope: explanatory outputs, training material, onboarding documentation, tutorials.
- Allowed tools: Read, Grep, Glob, Write/Edit on docs/ and learning artifacts only.
- Prohibitions:
  - no presentation of unverified examples as factual case studies;
  - no oversimplification that contradicts canon (e.g. presenting "ask the user" as default when CANON.md v1.2 §6 Question-Audit forbids it on safe commands);
  - no inclusion of bank-project specifics in factory-level educational content.
- Enforcement level: L0–L1 (documented; runs in CI when integrated with docs workflows).

## business

- Scope: roadmaps, decision briefs, strategic notes, prioritisation, P0/P1/P2 ladders (PATTERNS.md §3.6).
- Allowed tools: Read, Grep, Glob, Write/Edit on docs/strategy and decision-record artifacts.
- Prohibitions:
  - no presentation of multiple options to the user (CANON.md axiom §2);
  - no recommendation without an explicit Decision-Brief (CANON.md v1.2 §4) backing it internally;
  - no business decision that ignores Plan-Risk-Block (CANON.md v1.2 §8) for destructive or irreversible actions;
  - no introduction of competing strategic doctrines outside the canon change procedure.
- Enforcement level: L0–L1 (documented; integrated into ADR and topology updates when relevant).

---

## Combination rules

- Modules are additive: more than one may be active simultaneously.
- On prohibition conflict, the strictest module wins.
- `financial` and `creative` simultaneously: money-safety prohibitions take precedence over creative freedom.
- `scientific` and `business` simultaneously: epistemic accuracy takes precedence over strategic framing.

## Forbidden modules (verbatim from CANON.md v1.3 §11)

The following modules are forbidden and must be refused on load:

- legal
- legalfr
- legaleu
- legalqa
- ultralegal
- academic-legal
- medical — excluded (non-legal, out-of-scope per CANON.md §11): medical-domain требует licensed practitioners для clinical guidance; конфликт с harm-content-safety. Sprint 2 (B3), Universalnyi v4.15.1 line 658.

Any other legal-domain or court-research module falls under the same prohibition.

## Anchoring

This document realises CANON.md v1.3 §6 (Module/Override Operational Definitions requirement) for the module list in CANON.md v1.3 §11. Without this document, the module names in §11 are not part of the canon.
