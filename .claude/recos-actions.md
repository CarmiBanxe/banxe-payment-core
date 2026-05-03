# GitHub Actions — рекомендации (PR review + tests)

**Текущее состояние:** есть `ci.yml`, `claude.yml` (только по @claude), `factory-guard.yml`.

---

## P0 — критично, чинить сейчас

### 1. Semgrep `continue-on-error: true` → hard fail
`ci.yml:38` нарушает **I-12 (Validators = source of truth)** и **I-15 (no AGPLv3)**. BANXE-инварианты должны блокировать merge.
```yaml
- name: Semgrep BANXE rules
  run: semgrep --config .semgrep/rules.yaml src/ --error
  # убрать continue-on-error
```

### 2. Coverage gate не enforced в CI
`pytest` запускается без `--cov-fail-under`. Добавить явно (pyproject уже имеет 80, но CI должен это echo'ить):
```yaml
- name: pytest (coverage ≥80%)
  run: pytest --tb=short --cov-fail-under=80 --junitxml=junit.xml
- uses: dorny/test-reporter@v1
  if: always()
  with: { name: pytest, path: junit.xml, reporter: java-junit }
```

### 3. Concurrency control (нет — деньги горят)
Добавить в `ci.yml` сверху:
```yaml
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
```

---

## P1 — auto PR review

### 4. Авто-ревью Claude на каждый PR (без @claude)
Сейчас Claude отвечает только по mention. Добавить `claude-review.yml`:
```yaml
name: Claude PR Review
on:
  pull_request:
    types: [opened, synchronize, reopened]
jobs:
  review:
    runs-on: ubuntu-latest
    permissions: { pull-requests: write, contents: read }
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          prompt: |
            Review this PR against /home/mmber/banxe/banxe-payment-core/.claude/CLAUDE.md
            invariants. Block if: I-01/I-02/I-04/I-10/I-15 violated, float used for money,
            mocks added to integration tests, secrets committed.
```

### 5. CODEOWNERS — auto-routing review requests
`.github/CODEOWNERS`:
```
*                       @MorielCarmi
src/banxe/compliance/   @MorielCarmi @mlro
src/banxe/settlement/   @MorielCarmi  # IPM parser = ядро
.github/                @MorielCarmi
```

---

## P2 — security & dependency hygiene

### 6. Gitleaks в CI (defense in depth — pre-commit можно обойти)
```yaml
- uses: gitleaks/gitleaks-action@v2
  env: { GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }} }
```

### 7. pip-audit для CVE в зависимостях
```yaml
- run: pip install pip-audit && pip-audit --strict
```

### 8. Dependabot — `.github/dependabot.yml`
```yaml
version: 2
updates:
  - package-ecosystem: pip
    directory: "/"
    schedule: { interval: weekly }
  - package-ecosystem: github-actions
    directory: "/"
    schedule: { interval: weekly }
  - package-ecosystem: docker
    directory: "/docker"
    schedule: { interval: weekly }
```

---

## P3 — performance / DX

### 9. Path filters — не гонять CI на docs-only PR
```yaml
on:
  pull_request:
    paths-ignore: ["**.md", "docs/**", "CHANGELOG.md"]
```

### 10. Matrix Python 3.11 + 3.12 (стек заявлен 3.11+)
```yaml
strategy:
  matrix: { python-version: ["3.11", "3.12"] }
```

### 11. PR coverage comment (визуальная дельта)
```yaml
- uses: orgoro/coverage@v3.2
  with:
    coverageFile: coverage.xml
    token: ${{ secrets.GITHUB_TOKEN }}
    thresholdAll: 0.80
```

---

## Сводка по приоритетам

| # | Действие | Эффект | Усилие |
|---|----------|--------|--------|
| 1 | Semgrep hard-fail | Блок I-12/I-15 нарушений | 1 строка |
| 2 | junit + coverage gate в CI | Видимость падений | 5 мин |
| 3 | Concurrency cancel | Экономия CI minutes | 3 строки |
| 4 | Auto Claude review | PR-ревью без mention | новый workflow |
| 5 | CODEOWNERS | Auto-assign reviewers | 1 файл |
| 6 | Gitleaks в CI | Защита от утечек | 1 step |
| 7 | pip-audit | CVE в deps | 1 step |
| 8 | Dependabot | Auto-обновления | 1 файл |
| 9 | Path filters | -50% CI runs | 2 строки |
| 10 | Matrix 3.11/3.12 | Совместимость | 3 строки |
| 11 | Coverage PR comment | Видимость дельты | 1 step |

Хочешь — внесу P0 (#1–3) и заведу P1 (#4–5) прямо сейчас одним коммитом?
