# Red-Green Mode · Business Rule Discovery Gate / Web Evidence Rule / Evidence Ledger

> **When to read**: whenever the task touches finance, HR, contracts, invoices, payroll, SOPs, operations templates, support policies, permission matrices, spreadsheet formulas, or customer-facing deliverables — `PRECHECK` must clear this gate before entering `BASELINE`.

> The whole block below is lifted verbatim from SKILL.md (v0.19.0 slim-down; see the references index table in SKILL.md). Only its location changed — not a word of the text.

## Business Rule Discovery Gate

If the task touches **finance, HR, contracts, invoices, payroll, SOPs, operations templates, support policies, permission matrices, spreadsheet formulas, or customer-facing deliverables**, don't mistake "the existing tests are green" for "the business logic is right". `PRECHECK` must run one round of business-rule discovery before moving on to `BASELINE`:

1. **Scan the evidence sources**: the file at hand, adjacent usage docs, contracts / employee handbook / compensation policy, past templates, past invoices, the README, sibling files of the same kind, field names and formula comments.
2. **Extract the business invariants**: turn each rule you find into a verifiable assertion — e.g. "with 20 days attended, base pay is prorated by calendar days, not the full 6000".
3. **Add a semantic smoke test / targeted test**: at least one assertion must check the key output, the amount, the archived content, or the side effect. Checking that the script runs or the HTML compiles is not enough.
4. **Stop when the evidence is thin**: if a rule could only exist in the user's head, or the sources contradict each other, tag it `TEST_QUESTION`, list the candidate rules and their evidence, and wait for the user to confirm. Don't call it yourself.
5. **Report rule coverage**: the final report must state which business rules this run covered and which remain unconfirmed.

This gate isn't here to make the agent divine business truth out of thin air. It's here to stop "syntax green / generation green / UI green" from masking a wrong business rule. For anything involving money, people, contracts, or promises to customers, turn the business rules into something the judge can check first.

**Business rule discovery sub-flow** (walk through it explicitly whenever this gate fires):

```text
RULE_DISCOVERY → EVIDENCE_GATHERING → ASSERTION_DRAFT → BASELINE
```

| Sub-state | Exit condition | On failure |
|---|---|---|
| `RULE_DISCOVERY` | The business rules / formulas / edge cases this task could affect are listed | No rules found, yet the task clearly has business consequences → `TEST_QUESTION` |
| `EVIDENCE_GATHERING` | Every rule has an evidence source: local SSOT or compliant external evidence | Evidence conflicts or is missing → `TEST_QUESTION` |
| `ASSERTION_DRAFT` | Every key rule becomes at least one targeted test / semantic smoke / fixture assertion | Can't be verified → add a verifier first, or stop |
| `BASELINE` | The new assertions take part in the red-green loop and their red/green status is recorded | Hand off to the regular state machine |

### Web Evidence Rule

The business rule discovery gate checks the local SSOT first. But in the cases below, use whatever web search / browsing MCP is available (Brave Search, a browser) to gather more evidence, then turn the conclusion into an assertion. The test: if an external fact has a **>10% chance of having changed**, don't trust memory — confirm it online.

**Go online when:**
- The rule depends on an **external public fact or industry norm** and the local files say nothing definite: statutory holidays, tax / labor law / payroll compliance, bank / FX / payment-rail rules, cross-border platform policies, industry norms for support SLAs.
- The rule can change over time: platform policies, API and library docs, regulations, prices, exchange rates, fee schedules, holiday calendars, the behavior of browsers / desktop apps / third-party tools.
- Local evidence contradicts itself and you need an **official / first-party source** to check the external fact.
- The task integrates with a third-party system or web behavior (a desktop app's URI scheme, a SaaS API, browser automation) and you need to confirm the current official docs or the real page behavior.
- The task will bake an external fact into a long-lived template, an automation, a contract annex, or billing logic, and that fact isn't internal SSOT.

**Stay offline, or treat the web as reference only, when:**
- The rule is an internal company agreement, a customer contract, an employee's salary, a project-specific SOP, or established invoicing practice — internal SSOT wins. The web supplies background; it never overrides an internal rule.
- Private or customer-sensitive data is involved — never send sensitive content to a search engine. If you must view it in a logged-in browser, stay inside the authorized scope, keep it read-only where you can, and avoid exposing accounts, keys, or PII.
- No authoritative source turns up — don't settle it with search results. Tag `TEST_QUESTION` and list the candidate rules with their evidence.
- Never put a live network / search / browser call into the regular red-green loop as a verifier (it will be flaky). Do it this way instead: gather evidence online → freeze it into a local fixture / assertion / smoke test → then run the loop. Live network is allowed as the subject under test only when the task itself is testing a third-party page's or API's online behavior, and you must flag the flakiness risk.

**Source priority:**
1. Explicit user instructions / internal SSOT / contracts / past invoices
2. Official docs; first-party government, platform, bank, or vendor sources
3. Authoritative industry material
4. Ordinary web pages / search snippets (leads only)

The report must spell out which local evidence you checked, which external sources you used, the date you looked them up, and how the external evidence became a test or semantic smoke. Never let "I found it online" stand in for a verifier — going online only finds the rule; it still has to land as a verifiable assertion.

### Evidence Ledger (business / web evidence log)

When this gate fires, keep an evidence log alongside the run ledger, so "I found a web page" never gets mistaken for truth. Record at minimum:

| Rule | Source type | Source / URL / file | Authority | Checked at | Assertion created |
|---|---|---|---|---|---|
| `<business rule>` | `local_ssot / web_official / web_secondary / user_confirmed` | `<path or URL>` | `internal / official / authoritative / weak` | `<date>` | `<test or smoke name>` |

Rules:
- A `weak` source can't decide a business rule on its own; it's a lead, nothing more.
- Every rule that affects an amount, personnel, a contract, or a promise to a customer needs an `Assertion created`. Without one, stay at `ASSERTION_DRAFT`.
- The final report must cite a summary of the Evidence Ledger.
