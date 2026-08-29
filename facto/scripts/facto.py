#!/usr/bin/env python3
"""facto — a quote-backed fact ledger over CozoDB.

Usage: python scripts/facto.py <command> [args]
Run with no arguments for the command list.
"""

import argparse
import json
import os
import shutil
import sys

import store
from store import connect, query, digest, normalize, now


def out(*a):
    print(*a)


# --- commands ---------------------------------------------------------------

def cmd_init(args):
    db = connect(create=True)
    store.init(db)
    out(f"initialized store at {store.db_path()}")


def cmd_add_source(args):
    db = connect()
    text = open(args.path, encoding="utf-8", errors="replace").read()
    sha = digest(text)
    sid = digest("source", sha, args.title)
    dest = os.path.join(store.sources_dir(), f"{sid}.txt")
    os.makedirs(store.sources_dir(), exist_ok=True)
    shutil.copyfile(args.path, dest)
    db.run(
        "?[id, title, uri, path, sha, added_at] <- $rows :put source",
        {"rows": [[sid, args.title, args.uri or "", dest, sha, now()]]},
    )
    out(f"source_id {sid}")
    out(f"  title  {args.title}")
    out(f"  stored {dest}")


def cmd_sources(args):
    db = connect()
    rs = query(db, "?[id, title, uri, added_at] := *source{id, title, uri, added_at}")
    if not rs:
        out("no sources registered")
    for sid, title, uri, added in sorted(rs, key=lambda r: r[3]):
        out(f"{sid}  {title}" + (f"  <{uri}>" if uri else ""))


def cmd_predicates(args):
    db = connect()
    rs = query(
        db,
        "?[name, description, functional, object_type] := "
        "*predicate{name, description, functional, object_type}",
    )
    if not rs:
        out("no predicates declared yet — declare them before extracting")
        return
    out("declared predicates:")
    for name, desc, func, otype in sorted(rs):
        flags = []
        if func:
            flags.append("functional")
        if otype == "entity":
            flags.append("object=entity")
        tag = f"  [{', '.join(flags)}]" if flags else ""
        out(f"  {name}{tag}\n      {desc}")
    props = query(db, "?[name, rationale] := *proposal{name, rationale}")
    if props:
        out("\nopen proposals (not usable until declared):")
        for name, rationale in sorted(props):
            out(f"  {name} — {rationale}")


def cmd_declare(args):
    db = connect()
    db.run(
        "?[name, description, functional, object_type, declared_at] <- $rows :put predicate",
        {
            "rows": [[
                args.name,
                args.desc,
                bool(args.functional),
                args.object_type,
                now(),
            ]]
        },
    )
    db.run("?[name] <- $rows :rm proposal", {"rows": [[args.name]]})
    out(f"declared {args.name}")


def cmd_propose(args):
    db = connect()
    db.run(
        "?[name, rationale, quote, source_id, proposed_at] <- $rows :put proposal",
        {"rows": [[args.name, args.rationale, args.quote or "", args.source or "", now()]]},
    )
    out(f"proposed {args.name} — declare it before it can carry facts")


def cmd_proposals(args):
    db = connect()
    rs = query(db, "?[name, rationale, quote, source_id] := *proposal{name, rationale, quote, source_id}")
    if not rs:
        out("no open proposals")
    for name, rationale, quote, sid in sorted(rs):
        out(f"{name}\n  why:   {rationale}")
        if quote:
            out(f"  quote: {quote}")
        if sid:
            out(f"  src:   {sid}")


def cmd_load(args):
    db = connect()
    payload = json.load(open(args.path, encoding="utf-8"))
    facts = payload["facts"] if isinstance(payload, dict) else payload

    declared = {r[0] for r in query(db, "?[name] := *predicate{name}")}
    sources = {
        r[0]: r[1] for r in query(db, "?[id, path] := *source{id, path}")
    }
    texts = {}

    accepted, rejected = [], []
    for i, f in enumerate(facts):
        def reject(reason):
            rejected.append((i, f.get("pred", "?"), f.get("subject", "?"), reason))

        missing = [k for k in ("pred", "subject", "object", "source_id", "quote") if not f.get(k)]
        if missing:
            reject(f"missing field(s): {', '.join(missing)}")
            continue
        if f["pred"] not in declared:
            reject(f"predicate '{f['pred']}' is not declared — declare or propose it")
            continue
        sid = f["source_id"]
        if sid not in sources:
            reject(f"unknown source_id '{sid}'")
            continue
        if sid not in texts:
            texts[sid] = normalize(
                open(sources[sid], encoding="utf-8", errors="replace").read()
            )
        if normalize(f["quote"]) not in texts[sid]:
            reject("quote is not present verbatim in the source")
            continue
        fid = digest(f["pred"], f["subject"], f["object"], sid, normalize(f["quote"]))
        accepted.append([
            fid, f["pred"], str(f["subject"]), str(f["object"]), sid,
            f["quote"], now(), "", "",
        ])

    if accepted:
        db.run(
            "?[id, pred, subject, object, source_id, quote, asserted_at, "
            "retracted_at, retract_reason] <- $rows :put fact",
            {"rows": accepted},
        )
    out(f"accepted {len(accepted)} fact(s)")
    for row in accepted:
        out(f"  {row[0]}  {row[1]}({row[2]}, {row[3]})")
    if rejected:
        out(f"\nrejected {len(rejected)} fact(s):")
        for i, pred, subject, reason in rejected:
            out(f"  [{i}] {pred}({subject}, ...) — {reason}")
        out("\nRe-extract rejected facts from the source. Do not edit the JSON to")
        out("make it pass; a hand-patched quote is not a provenance record.")


def cmd_check(args):
    db = connect()
    titles = {r[0]: r[1] for r in query(db, "?[id, title] := *source{id, title}")}

    conflicts = query(db, store.CONFLICTS)
    out(f"== conflicts ({len(conflicts)}) ==")
    for pred, subject, o1, id1, q1, s1, o2, id2, q2, s2 in conflicts:
        out(f"\n{pred}({subject}) — functional, but two live values:")
        out(f"  {o1}  [{id1}]  {titles.get(s1, s1)}\n      \"{q1}\"")
        out(f"  {o2}  [{id2}]  {titles.get(s2, s2)}\n      \"{q2}\"")
    if conflicts:
        out("\nShow both quotes to the user and let them decide; retract the loser")
        out("with a reason rather than deleting it.")

    gaps = query(db, store.GAPS)
    out(f"\n== gaps ({len(gaps)}) ==   entities referenced but never described")
    for entity, pred, subject in sorted(gaps):
        out(f"  {entity}  (via {pred} from {subject})")

    singles = query(db, store.SINGLE_SOURCE)
    out(f"\n== single_source ({len(singles)}) ==   claims resting on one source")
    for subject, pred, _ in sorted(singles):
        out(f"  {pred}({subject})")


def cmd_retract(args):
    db = connect()
    rs = query(
        db,
        "?[pred, subject, object, retracted_at] := *fact{id: $id, pred, subject, object, retracted_at}",
        {"id": args.id},
    )
    if not rs:
        raise SystemExit(f"no fact with id {args.id}")
    pred, subject, obj, already = rs[0]
    if already:
        raise SystemExit(f"fact {args.id} was already retracted at {already}")
    db.run(
        "?[id, retracted_at, retract_reason] <- $rows :update fact",
        {"rows": [[args.id, now(), args.reason]]},
    )
    out(f"retracted {pred}({subject}, {obj})")
    out(f"  reason: {args.reason}")
    out("  the fact stays queryable — `show` still returns it")


def cmd_context(args):
    db = connect()
    titles = {r[0]: r[1] for r in query(db, "?[id, title] := *source{id, title}")}
    live = query(
        db,
        "?[id, pred, object, quote, source_id] := "
        "*fact{id, pred, subject: $s, object, quote, source_id, retracted_at: ''}",
        {"s": args.subject},
    )
    incoming = query(
        db,
        "?[pred, subject] := *fact{pred, subject, object: $s, retracted_at: ''}",
        {"s": args.subject},
    )
    if not live and not incoming:
        out(f"nothing on '{args.subject}'.")
        out("Say so rather than answering from memory — offer to ingest a source.")
        return
    out(f"# {args.subject}")
    for fid, pred, obj, quote, sid in sorted(live, key=lambda r: r[1]):
        out(f"\n{pred}: {obj}   [{fid}]")
        out(f'  "{quote}"')
        out(f"  — {titles.get(sid, sid)}")
    if incoming:
        out("\nreferenced by:")
        for pred, subject in sorted(incoming):
            out(f"  {subject} —{pred}→ {args.subject}")


def cmd_show(args):
    db = connect()
    rs = query(
        db,
        "?[pred, subject, object, quote, source_id, asserted_at, retracted_at, retract_reason] := "
        "*fact{id: $id, pred, subject, object, quote, source_id, asserted_at, "
        "retracted_at, retract_reason}",
        {"id": args.id},
    )
    if not rs:
        raise SystemExit(f"no fact with id {args.id}")
    pred, subject, obj, quote, sid, asserted, retracted, reason = rs[0]
    src = query(db, "?[title, uri] := *source{id: $id, title, uri}", {"id": sid})
    title, uri = src[0] if src else (sid, "")
    out(f"{pred}({subject}, {obj})")
    out(f'  quote:    "{quote}"')
    out(f"  source:   {title}" + (f"  <{uri}>" if uri else ""))
    out(f"  asserted: {asserted}")
    if retracted:
        out(f"  RETRACTED {retracted}: {reason}")


def cmd_query(args):
    db = connect()
    result = db.run(args.script)
    if isinstance(result, dict):
        headers = result.get("headers", [])
        rs = result.get("rows", [])
    else:
        headers, rs = list(result.columns), result.values.tolist()
    if headers:
        out("\t".join(str(h) for h in headers))
    for row in rs:
        out("\t".join(str(c) for c in row))


# --- entry point ------------------------------------------------------------

def main(argv):
    p = argparse.ArgumentParser(prog="facto", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the store").set_defaults(fn=cmd_init)

    s = sub.add_parser("add-source", help="register a text source")
    s.add_argument("path")
    s.add_argument("--title", required=True)
    s.add_argument("--uri", default="")
    s.set_defaults(fn=cmd_add_source)

    sub.add_parser("sources", help="list registered sources").set_defaults(fn=cmd_sources)

    sub.add_parser("predicates", help="list the predicate registry").set_defaults(
        fn=cmd_predicates
    )

    s = sub.add_parser("declare", help="declare a predicate")
    s.add_argument("name")
    s.add_argument("--desc", required=True)
    s.add_argument("--functional", action="store_true",
                   help="at most one true value per subject")
    s.add_argument("--object-type", default="literal", choices=["literal", "entity"])
    s.set_defaults(fn=cmd_declare)

    s = sub.add_parser("propose", help="file a predicate proposal instead of improvising")
    s.add_argument("name")
    s.add_argument("--rationale", required=True)
    s.add_argument("--quote", default="")
    s.add_argument("--source", default="")
    s.set_defaults(fn=cmd_propose)

    sub.add_parser("proposals", help="list open proposals").set_defaults(fn=cmd_proposals)

    s = sub.add_parser("load", help="load extracted facts from JSON")
    s.add_argument("path")
    s.set_defaults(fn=cmd_load)

    sub.add_parser("check", help="run conflicts, gaps and single_source").set_defaults(
        fn=cmd_check
    )

    s = sub.add_parser("retract", help="retract a fact with a reason")
    s.add_argument("id")
    s.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_retract)

    s = sub.add_parser("context", help="everything live about a subject")
    s.add_argument("subject")
    s.set_defaults(fn=cmd_context)

    s = sub.add_parser("show", help="one fact with full provenance")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("query", help="run raw CozoScript")
    s.add_argument("script")
    s.set_defaults(fn=cmd_query)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main(sys.argv[1:])
