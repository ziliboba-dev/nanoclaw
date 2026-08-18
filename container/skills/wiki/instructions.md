# Wiki

You maintain a persistent personal-knowledge wiki under `/workspace/agent/`, three layers: `sources/` (raw material, immutable), `wiki/` (pages you own — `index.md` catalogs everything, `log.md` is the append-only activity log), `wiki/schema.md` (this wiki's page format + ingest/lint rules).

Three operations: **ingest** (a source arrives), **query** (answer from the wiki), **lint** (health check). Run `/wiki` for the full workflow.

**Ingest discipline:** when the user sends multiple files or points at a folder, process them ONE AT A TIME. For each: read it, discuss takeaways, create/update all affected wiki pages, update `index.md` and `log.md` — fully finish before starting the next. Never batch-read several sources and write pages for all of them together; that produces shallow, generic pages instead of real integration into the existing wiki.
