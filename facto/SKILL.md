---
name: facto
description: Record research findings as quote-backed facts in a Datalog (CozoDB) store that detects contradictions, tracks belief revision over time, and assembles prompt context from verified facts instead of vector-similarity chunks. Use this skill whenever the user is doing research that spans multiple sessions or sources — literature reviews, competitive analysis, due diligence, investigations, long-running notes — or whenever they mention fact extraction, provenance, citations, knowledge graphs, contradiction detection, or "things I've learned so far." Also use it when the user complains that their notes have drifted, that they can't remember where a claim came from, or that an AI assistant keeps losing or inventing details across a long project. Prefer this over freeform markdown notes or a RAG index any time the accuracy of individual claims matters more than the fluency of the summary.
---

# Facto

Freeform notes and vector search both fail the same way over a long project: a
claim gets softened, merged, or invented, and nothing in the system notices.
This skill fixes the division of labour. The model does extraction — turning
prose into structured claims, which it is very good at. A Datalog store does
memory and inference — which it does without hallucinating.

The load path refuses any fact whose supporting quote is not literally present
in the source. That single mechanical gate catches most extraction drift before
it enters the record.

## Setup

**Requires [uv](https://docs.astral.sh/uv/).** There is no install step: the
CozoDB dependency is declared in a PEP 723 block at the top of `scripts/facto.py`,
so `uv run` builds and caches the environment on first use and every later run
starts from cache. Never invoke the scripts with a bare `python` — a stale
interpreter environment is exactly the drift this skill exists to prevent, and
the scripts will refuse rather than half-work.

Run them from the research directory; the store lands in `.facto/` (override
with the `FACTO_DIR` environment variable). Add `.facto/store.db` to version control if the team shares findings — it is
a single file — but keep `.facto/sources/` out of git if the sources are large
or licensed.

```bash
uv run scripts/facto.py init
```

## The loop

For each new source, work through these five steps in order. Do not skip step 4;
an unreviewed conflict queue is how a ledger quietly becomes as unreliable as
the notes it replaced.

### 1. Register the source

```bash
uv run scripts/facto.py add-source paper.txt --title "Rosen et al. 2019" --uri "https://doi.org/..."
```

Prints a `source_id`. The file is hashed and copied into `.facto/sources/`, so
later re-extraction works even if the original moves. Register plain text where
possible — quote verification compares against the stored bytes, so a PDF must
be converted to text first (the `pdf-reading` skill handles this) and the text
file is what gets registered.

### 2. Read the registry before extracting

```bash
uv run scripts/facto.py predicates
```

This is the step that keeps the schema from rotting. Left to itself, extraction
invents `sample_size`, then `n_participants`, then `enrolled_count` for the same
relation across three sessions, and no rule can see across them. Always load the
current registry into context before extracting, and match against it.

Declare predicates deliberately, few and general rather than many and specific:

```bash
uv run scripts/facto.py declare sample_size \
  --desc "Number of enrolled participants in a study" --functional

uv run scripts/facto.py declare contradicted_by \
  --desc "A study whose result fails to replicate this one" --object-type entity
```

`--functional` means at most one true value per subject. This is what powers
contradiction detection, so set it on anything single-valued: sample size,
publication year, founding date, price, headcount.

`--object-type entity` means the object is a thing worth researching in its own
right rather than a literal value. This powers gap detection.

### 3. Extract

Read the source and emit JSON. One object per claim:

```json
{"facts": [
  {"pred": "sample_size", "subject": "rosen_2019", "object": "412",
   "source_id": "164393cc1a278695",
   "quote": "The study enrolled 412 participants across three sites."}
]}
```

Rules for extraction, and the reasoning behind each:

- **The quote must be copied verbatim from the source.** Not paraphrased, not
  reconstructed from memory, not tidied up. The loader checks this literally
  (whitespace-insensitively), and a failed check means the fact gets rejected.
- **The quote must actually support the claim on its own.** Before emitting,
  reread the quote in isolation and ask whether someone seeing only that
  sentence would agree the fact follows. If it takes surrounding context to
  justify, widen the quote to include that context.
- **Subjects are stable slugs**, e.g. `rosen_2019`, `acme_corp`. Reuse existing
  subject strings — check with `facto.py context <name>` if unsure. Inconsistent
  subject naming breaks joins just as badly as inconsistent predicate naming.
- **Attribute to the source you actually read.** If a review reports another
  study's number, the source is the review, not the original paper. This is
  precisely the case where research notes go wrong, and it is the case the
  conflict rule is designed to surface.
- **Never guess a `source_id`.** Use the one printed by `add-source`.
- **When no registered predicate fits**, do not improvise one. File a proposal
  and move on:

```bash
uv run scripts/facto.py propose funding_source \
  --rationale "Funding matters for bias assessment across the whole corpus" \
  --quote "Supported by a grant from the Wellcome Trust" --source 164393cc
```

Then load:

```bash
uv run scripts/facto.py load facts.json
```

The output lists every rejection with a reason. Re-extract rejected facts rather
than editing the JSON to make it pass — a hand-patched quote is a provenance
record that no longer means anything.

### 4. Check, and resolve what it finds

```bash
uv run scripts/facto.py check
```

Three rules run:

- **conflicts** — two live facts disagree on a functional predicate. Resolve by
  retracting the wrong one with a reason. Never delete.
- **gaps** — an entity referenced as an object but never described as a subject.
  Usually a cited work you have not read yet. This is your reading queue.
- **single_source** — claims resting on exactly one source. Not errors, but the
  claims most worth corroborating before you build on them.

```bash
uv run scripts/facto.py retract 832771d6ca8bd166 \
  --reason "Okonkwo misquotes Rosen; primary source says 412"
```

Retraction sets a timestamp and reason. The fact stays queryable forever, which
is what lets you answer "what did I believe in March, and what changed my mind?"
Report conflicts to the user with both quotes side by side rather than silently
picking a winner — deciding which source is right is usually a judgement call
that depends on things the ledger cannot see.

### 5. Use the ledger as context

When the user asks a research question, pull facts from the ledger rather than
reasoning from your own recollection of earlier sessions:

```bash
uv run scripts/facto.py context rosen_2019
uv run scripts/facto.py show 408fbd0f888282e0
```

Everything returned is live and quote-backed. Cite the quote and source title in
your answer. If the ledger has nothing on a subject, say so and offer to ingest
a source, rather than filling the gap from memory — filling gaps from memory is
the exact failure this whole apparatus exists to prevent.

## Writing your own rules

The three built-in rules are a starting point. Most of the value in a mature
ledger comes from rules specific to the domain — transitive citation chains,
entity resolution, coverage matrices over a research question. Read
`references/cozoscript.md` for the syntax and worked examples, then:

```bash
uv run scripts/facto.py query "?[subject, object] := *fact{pred: 'studies', subject, object, retracted_at: ''}"
```

When a rule proves useful, add it to `scripts/store.py` next to `CONFLICTS` so
it runs as part of `check`.

## When this is the wrong tool

Say so plainly rather than setting it up anyway. A ledger costs real overhead
per source, and it only pays back across time and volume. For a single document,
a one-off summary, or a project where the user wants fluent prose more than
auditable claims, ordinary notes are the better answer. The break-even is
roughly: more than a handful of sources, or a project you will return to after
forgetting the details.
