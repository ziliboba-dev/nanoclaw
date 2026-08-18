---
name: wiki
description: Maintain the persistent personal-knowledge wiki in /workspace/agent/wiki/ (Karpathy LLM Wiki pattern). Use when the user sends a source to ingest (URL, PDF, image, note/text), asks a question that should be answered from the wiki, or asks for a wiki health check (lint).
---

# Personal Wiki

Three layers, all under `/workspace/agent/`:

- **`sources/`** — raw material (downloaded articles, PDFs, images, pasted text). Immutable. Read but never edit.
- **`wiki/`** — pages you own entirely: entities, concepts, summaries, comparisons. `wiki/index.md` catalogs everything; `wiki/log.md` is the append-only activity log.
- **`wiki/schema.md`** — this wiki's page format + ingest/lint rules (in Russian — write pages in Russian to match).

Read `wiki/schema.md` before your first ingest of a session — it defines the page template and conventions already agreed with the user.

## Operations

### Ingest (new source arrives)

**Critical — one source at a time.** If the user sends multiple files or points at a folder, process them individually: for each source, complete every step below before touching the next. Never batch-read several sources and then write pages for all of them together — that produces shallow, generic pages instead of real integration into the existing wiki.

Per source:

1. **Acquire full text**, not a summary:
   - URL, article/full text matters → `curl -sLo sources/<name>.<ext> "<url>"`, or `agent-browser` if the page needs JS rendering. Do not rely on `WebFetch` alone — it returns a summary, and the wiki needs the source text.
   - PDF / office doc → convert via the anydoc CLI (see `onecli-gateway`/anydoc skill if installed) into `sources/`, then read the converted text.
   - Image → save to `sources/`, read directly (native vision) — no conversion needed.
   - Pasted text/notes → write straight to `sources/<name>.md`.
2. **Read the source in full.**
3. **Discuss takeaways with the user** briefly before writing pages — confirm you're extracting the right things.
4. **Identify affected entities/concepts** (typically 3-15 per source per `wiki/schema.md`).
5. **Create or update each page** following `wiki/schema.md`'s template — add `[[wikilink]]` cross-references to related existing pages.
6. **Update `wiki/index.md`** — add new pages, bump the source/page counts.
7. **Append to `wiki/log.md`**: `## [YYYY-MM-DD] ingest | <source title>`.
8. Only then move to the next source.

### Query (user asks a question)

1. Read `wiki/index.md` first to locate relevant pages — don't grep the whole wiki blind.
2. Read the specific pages, synthesize an answer with citations back to `wiki/<page>.md` (and ultimately `sources/`).
3. If the answer is substantial and reusable, offer to file it back into the wiki as a new page (comparison, synthesis) — ask before creating it.
4. Append to `wiki/log.md`: `## [YYYY-MM-DD] query | <question, short>`.

### Lint (periodic or on request)

Walk `wiki/*.md` and check, per `wiki/schema.md`'s rules:
- Pages without sources → mark `[unverified]`
- Contradictions between pages → add a "Противоречия" section
- Pages older than 90 days with no update → mark `[stale]`
- Orphan pages (no inbound `[[links]]`) → check relevance, link in or flag

Report findings to the user with suggested follow-up sources/investigations rather than silently fixing everything. Append to `wiki/log.md`: `## [YYYY-MM-DD] lint | <N issues found>`.

## Notes

- `grep "^## \[" wiki/log.md | tail -5` — quick recent-activity check before starting a session.
- Don't over-engineer structure beyond `wiki/schema.md` — extend the schema itself (with the user) if a new source type needs new conventions, rather than improvising per-page.
