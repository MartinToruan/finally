# Change Review Log

Automated reviews of all changes since the last commit, appended by the project's Stop hook after each session.

---

## 2026-08-24 — `planning/PLAN.md` (+32 lines)

**Change summary:** Added a new `## 13. Doc Review — Questions, Clarifications & Simplification Opportunities` section to the end of `planning/PLAN.md`, in response to a `/doc-review PLAN.md` command. Purely additive — no existing text was edited or removed. Contains 9 numbered questions/clarifications and 3 simplification suggestions about the plan itself (watchlist/position price-coverage gap, unvalidated/unknown tickers, an underspecified schema enum, unbounded chat history, no cap on LLM-issued trades per response, a bind-mount vs. named-volume inconsistency between §4 and §11, the unauthenticated + optional-public-deploy combination, unbounded `portfolio_snapshots` growth, and a stale skill name reference).

**Assessment:**
- **Correctness of claims:** Spot-checked each numbered item against the rest of `PLAN.md`. All hold up — e.g. §8's `DELETE /api/watchlist/{ticker}` genuinely says nothing about closing an open position (item 1), §11's `docker run -v finally-data:/app/db` is genuinely a named volume where §4 describes a bind-mount-shaped `db/` directory (item 6), and §9 does say "cerebras-inference skill" while the only skill actually registered in this environment is `cerebras` (item 9).
- **Scope discipline:** The section is explicitly framed as advisory ("Nothing here has been decided") and doesn't silently rewrite any normative part of the plan — appropriate for a doc-review pass rather than a plan revision.
- **Formatting:** Follows the existing document's heading/numbering conventions (`## N. Title`, bold lead-ins, horizontal rule before the new section). Renders correctly as markdown.
- **Risk:** None — additive documentation only, no code or config touched, nothing that affects build/run behavior.

**Findings requiring action:** None blocking. Two low-priority follow-ups worth someone picking up eventually (not done here, since this is a review of the diff, not a fix pass):
1. The stale `cerebras-inference` → `cerebras` skill-name reference in §9 is a real doc bug independent of this review section noting it; worth a one-line fix in §9 itself next time PLAN.md is touched.
2. Item 1 (watchlist/position price-coverage gap) is the most substantive open question in the batch and would be worth resolving before the Market Data / Portfolio agents implement price caching, since it affects the SSE ticker-set contract those two areas both depend on.

**Verdict:** Change is sound as a documentation addition — no corrections needed to what was written.

---

## 2026-08-24 — `planning/PLAN.md` (cumulative, +51/-14 lines vs. `6b568a9`)

**Change summary:** Supersedes the entry above — same file, now with the §13 review questions resolved and incorporated throughout the spec, plus one follow-up correction. In order:
1. §6 Market Data: added a deterministic hash-derived seed price for tickers outside the 10 defaults, and a new "Ticker Validation" subsection (1–5 uppercase letters, 400 on malformed input).
2. §6 SSE Streaming: clarified the SSE-priced set is exactly the watchlist, and *why* that stays true (removal is blocked while a position is open).
3. §8 API table: `GET /api/portfolio/history` now defaults to last 24h with `since`/`limit` params; `POST /api/watchlist` documents its format validation; `DELETE /api/watchlist/{ticker}` now returns 409 if a position is open.
4. §9 LLM Integration: fixed the stale `cerebras-inference` skill name to `cerebras` (both occurrences); pinned chat history to the last 20 messages; capped `trades` at 10 per response with overflow reported as an error; documented the `watchlist_changes.action` enum (`add`/`remove`) and made `"remove"` go through the same open-position validation as the manual DELETE endpoint.
5. §11 Docker & Deployment: switched the volume example from a named Docker volume to a bind mount (`-v "$(pwd)/db:/app/db"`, with a PowerShell variant) to match §4's description of `db/` as a real repo directory; added a warning against exposing the app publicly without a shared secret or IP allowlist, since it has no auth.
6. §13: rewritten from open questions into a resolved log, each item pointing at where it landed; noted that three simplification suggestions (renaming `backend/db/`, phasing simulator complexity, consolidating Docker tooling) were reviewed and declined by the user.

Notably, item 1 above (the watchlist/position price-coverage question) was first resolved one way — pricing the union of watchlist ∪ open positions, with SSE/§8 text to match — then reverted in a follow-up turn after the user picked the other option from the original write-up: block watchlist removal outright while a position is open. The diff reviewed here reflects that final, user-confirmed choice (§8 returns 409; §9's `watchlist_changes` "remove" is validated the same way; §13 item 1 records "the user chose (b)"), not the intermediate union-pricing version.

**Assessment:**
- **Internal consistency:** Cross-checked the interdependent spots — §6's "removal is blocked" claim, §8's 409 on `DELETE`, and §9's "remove... is rejected" all agree on the same behavior; §8's `POST /api/watchlist` format note (1–5 uppercase letters) matches §6's new Ticker Validation subsection; §9's "at most 10 trades" bullet text and schema comment agree; §11's bind-mount command matches §4's existing description of `db/`. No contradictions found.
- **Correctness of the skill-name fix:** `cerebras` is confirmed as the actual registered skill name in this environment (checked against the available-skills listing), so the `cerebras-inference` → `cerebras` correction is accurate.
- **Markdown table integrity:** New text added inside `§8` table cells uses `/` rather than `|` for alternatives (e.g. "since / limit"), so no table rows were broken.
- **Scope discipline:** §13 was rewritten to a resolution log rather than deleted, preserving a traceable record of what was asked and decided — reasonable given `REVIEW.md` already serves as the durable audit trail for this repo.
- **Risk:** None — still a documentation-only change; no code, config, or schema files were touched.

**Findings requiring action:** None. The one substantive design reversal (union-pricing → block-removal) is a deliberate, user-directed choice, not an inconsistency — it's correctly reflected everywhere it needed to be.

**Verdict:** Change is sound and internally consistent with the final (block-removal) design decision.
