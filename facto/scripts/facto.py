#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["pycozo[embedded]"]
# ///
"""facto — a quote-backed fact ledger over CozoDB.

Usage: uv run scripts/facto.py <command> [args]
Run with no arguments for the command list.

The dependency is declared above (PEP 723) rather than in prose, so uv builds
and caches the environment on first run and there is no install step to forget.
"""

import argparse
import json
import os
import shutil
import socket
import sys
from datetime import datetime, timedelta, timezone

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
    origin = os.path.abspath(args.path)
    stamp = now()
    # verified_at starts at the registration time: the bytes were just read and
    # hashed, so this is the last moment anyone actually saw them.
    db.run(
        "?[id, title, uri, path, origin, sha, added_at, verified_at] <- $rows :put source",
        {"rows": [[sid, args.title, args.uri or "", dest, origin, sha, stamp, stamp]]},
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


def author(args):
    """Who is asserting this. Six agents share a store; a fact with no author
    cannot answer "who believed this, and when did they stop."

    Taken from --as, else FACTO_AGENT, else AGENT_MAIL_NAME - the same address an
    agent already claims on the mail network, so there is nothing new to invent.
    Never guessed from the shell or the host: a wrong author is worse than none,
    because it is believed.
    """
    who = getattr(args, "as_", "") or os.environ.get("FACTO_AGENT", "") \
        or os.environ.get("AGENT_MAIL_NAME", "")
    if not who:
        raise SystemExit(
            "no author: pass --as <name> (or set FACTO_AGENT / AGENT_MAIL_NAME).\n"
            "Every fact records who asserted it; that is what makes a"
            " disagreement answerable."
        )
    return who


def cmd_load(args):
    db = connect()
    who = author(args)
    payload = json.load(open(args.path, encoding="utf-8"))
    facts = payload["facts"] if isinstance(payload, dict) else payload

    declared = {r[0] for r in query(db, "?[name] := *predicate{name}")}
    sources = {
        r[0]: r[1] for r in query(db, "?[id, path] := *source{id, path}")
    }
    texts = {}
    host = socket.gethostname()
    stamp = now()

    accepted, rejected = [], []
    for i, f in enumerate(facts):
        def reject(reason):
            rejected.append((i, f.get("pred", "?"), f.get("subject", "?"), reason))

        missing = [k for k in ("pred", "subject", "object") if not f.get(k)]
        if missing:
            reject(f"missing field(s): {', '.join(missing)}")
            continue
        if f["pred"] not in declared:
            reject(f"predicate '{f['pred']}' is not declared — declare or propose it")
            continue

        quote, command = f.get("quote", ""), f.get("command", "")
        if bool(quote) == bool(command):
            reject("give exactly one of 'quote' or 'command' as the evidence")
            continue

        sid = f.get("source_id", "")
        cwd = expect = ""

        if quote:
            # The original gate, unchanged: the quote must be in the source.
            if not sid:
                reject("a quote needs the source_id it was copied from")
                continue
            if sid not in sources:
                reject(f"unknown source_id '{sid}'")
                continue
            if sid not in texts:
                texts[sid] = normalize(
                    open(sources[sid], encoding="utf-8", errors="replace").read()
                )
            if normalize(quote) not in texts[sid]:
                reject("quote is not present verbatim in the source")
                continue
            evidence = normalize(quote)
        else:
            # The same gate for a command: it has to run, here and now, and its
            # output is what gets recorded. A quote cannot distinguish 154 from
            # 155 on the same line; `grep -c` can, every time it is re-run.
            if sid and sid not in sources:
                reject(f"unknown source_id '{sid}'")
                continue
            cwd = os.path.abspath(os.path.expanduser(f.get("cwd") or os.getcwd()))
            ok, output = store.run_command(command, cwd)
            if not ok:
                reject(f"command failed, so there is no evidence: {output}")
                continue
            declared_expect = f.get("expect")
            if declared_expect is not None and str(declared_expect).strip() != output:
                reject(
                    f"command output {output!r} is not the declared expect "
                    f"{str(declared_expect).strip()!r}"
                )
                continue
            expect = output
            evidence = f"{command}\n{cwd}\n{output}"

        fid = digest(f["pred"], f["subject"], f["object"], sid, evidence)
        accepted.append([
            fid, f["pred"], str(f["subject"]), str(f["object"]), sid,
            quote, command, cwd, expect, host if command else "",
            who, stamp, stamp, "", "", "",
        ])

    if accepted:
        db.run(
            "?[id, pred, subject, object, source_id, quote, command, cwd, expect, "
            "host, asserted_by, asserted_at, verified_at, retracted_at, "
            "retract_reason, retracted_by] <- $rows :put fact",
            {"rows": accepted},
        )
    out(f"accepted {len(accepted)} fact(s) as {who}")
    for row in accepted:
        how = f"= {row[8]}" if row[6] else "(quoted)"
        out(f"  {row[0]}  {row[1]}({row[2]}, {row[3]})  {how}")
    if rejected:
        out(f"\nrejected {len(rejected)} fact(s):")
        for i, pred, subject, reason in rejected:
            out(f"  [{i}] {pred}({subject}, ...) — {reason}")
        out("\nRe-extract rejected facts from the source. Do not edit the JSON to")
        out("make it pass; a hand-patched quote is not a provenance record, and a")
        out("command edited until it agrees is not evidence either.")


def cmd_recheck(args):
    """Re-verify every fact against the thing it cited.

    Quote-backed facts are checked against the file they came from; command-backed
    facts are checked by running the command again. The second is the one that
    makes counts checkable at all - a quote cannot tell 154 from 155 on the same
    line, and a re-run can.

    A fact that still holds gets a fresh verified_at, which is what keeps it out
    of the stale list; that is the only thing this command writes.
    """
    db = connect()
    titles = {r[0]: r[1] for r in query(db, "?[id, title] := *source{id, title}")}
    srcs = query(db, "?[id, title, origin, sha] := *source{id, title, origin, sha}")

    unchanged, changed, unverifiable = [], [], []
    verified_ids, verified_srcs = [], []
    stamp = now()

    for sid, title, origin, sha in sorted(srcs, key=lambda r: r[1]):
        if not origin:
            unverifiable.append((title, "no origin recorded (registered before recheck existed)"))
            continue
        if not os.path.exists(origin):
            unverifiable.append((title, f"origin is gone: {origin}"))
            continue
        try:
            current = open(origin, encoding="utf-8", errors="replace").read()
        except OSError as e:
            unverifiable.append((title, f"unreadable: {e}"))
            continue

        live = query(
            db,
            "?[id, pred, subject, object, quote] := "
            "*fact{id, pred, subject, object, quote, source_id: $s, command: '', "
            "retracted_at: ''}",
            {"s": sid},
        )
        if digest(current) == sha:
            unchanged.append(title)
            verified_srcs.append([sid, stamp])
            verified_ids.extend(f[0] for f in live)
            continue

        norm = normalize(current)
        stale = [f for f in live if normalize(f[4]) not in norm]
        verified_ids.extend(f[0] for f in live if normalize(f[4]) in norm)
        changed.append((title, origin, len(live) - len(stale), stale))

    # Command-backed facts: run each one again.
    cmds = query(
        db,
        "?[id, pred, subject, object, command, cwd, expect, asserted_by] := "
        "*fact{id, pred, subject, object, command, cwd, expect, asserted_by, "
        "retracted_at: ''}, command != ''",
    )
    still, drifted, unrunnable = [], [], []
    for fid, pred, subject, obj, command, cwd, expect, by in sorted(cmds, key=lambda r: r[1]):
        ok, output = store.run_command(command, cwd)
        if not ok:
            unrunnable.append((fid, pred, subject, command, output))
        elif output == expect:
            still.append((fid, pred, subject, obj))
            verified_ids.append(fid)
        else:
            drifted.append((fid, pred, subject, obj, by, command, expect, output))

    out(f"== unchanged sources ({len(unchanged)}) ==")
    for title in unchanged:
        out(f"  {title}")

    out(f"\n== changed sources ({len(changed)}) ==")
    for title, origin, still_ok, stale in changed:
        out(f"\n{title}\n  {origin}")
        out(f"  {still_ok} live fact(s) still supported by the current file")
        if not stale:
            continue
        out(f"  {len(stale)} live fact(s) whose quote is GONE:")
        for fid, pred, subject, obj, quote in stale:
            out(f"    {fid}  {pred}({subject}, {obj})")
            out(f'      "{quote}"')

    out(f"\n== commands re-run ({len(still) + len(drifted) + len(unrunnable)}) ==")
    for fid, pred, subject, obj in still:
        out(f"  holds     {pred}({subject}, {obj})")
    for fid, pred, subject, obj, by, command, expect, output in drifted:
        out(f"\n  MOVED     {pred}({subject}, {obj})   [{fid}]  asserted by {by or '?'}")
        out(f"    $ {command}")
        out(f"    was {expect!r}, now {output!r}")
    for fid, pred, subject, command, why in unrunnable:
        out(f"\n  UNRUNNABLE {pred}({subject})   [{fid}]")
        out(f"    $ {command}")
        out(f"    {why}")

    out(f"\n== unverifiable sources ({len(unverifiable)}) ==")
    for title, reason in unverifiable:
        out(f"  {title} — {reason}")

    # Stamp what still holds, so `check` stops nagging about it.
    if verified_ids:
        db.run(
            "?[id, verified_at] <- $rows :update fact",
            {"rows": [[i, stamp] for i in sorted(set(verified_ids))]},
        )
    if verified_srcs:
        db.run("?[id, verified_at] <- $rows :update source", {"rows": verified_srcs})

    total_stale = sum(len(c[3]) for c in changed) + len(drifted)
    if total_stale:
        out(f"\n{total_stale} fact(s) no longer match what they cited. Retract each with")
        out("a reason, then re-extract from the source or re-run as it is now. Do not")
        out("edit the stored snapshot or the expected output to make them pass.")


def cmd_check(args):
    db = connect()
    titles = {r[0]: r[1] for r in query(db, "?[id, title] := *source{id, title}")}

    def cite(quote, command, sid):
        """One line naming the evidence, whichever kind it is."""
        if command:
            return f"$ {command}"
        return f'"{quote}"  — {titles.get(sid, sid)}'

    conflicts = query(db, store.CONFLICTS)
    out(f"== conflicts ({len(conflicts)}) ==")
    for pred, subject, o1, id1, q1, c1, s1, b1, o2, id2, q2, c2, s2, b2 in conflicts:
        out(f"\n{pred}({subject}) — functional, but two live values:")
        out(f"  {o1}  [{id1}]  asserted by {b1 or '?'}")
        out(f"      {cite(q1, c1, s1)}")
        out(f"  {o2}  [{id2}]  asserted by {b2 or '?'}")
        out(f"      {cite(q2, c2, s2)}")
    if conflicts:
        out("\nShow both to the user with who asserted each, and let them decide;")
        out("retract the loser with a reason rather than deleting it.")

    gaps = query(db, store.GAPS)
    out(f"\n== gaps ({len(gaps)}) ==   entities referenced but never described")
    for entity, pred, subject in sorted(gaps):
        out(f"  {entity}  (via {pred} from {subject})")

    singles = query(db, store.SINGLE_SOURCE)
    out(f"\n== single_source ({len(singles)}) ==   claims resting on one source")
    for subject, pred, _ in sorted(singles):
        out(f"  {pred}({subject})")

    days = args.days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    stale = query(db, store.STALE, {"cutoff": cutoff})
    out(f"\n== stale ({len(stale)}) ==   not verified in {days} day(s)")
    for fid, pred, subject, obj, verified_at, by, sid, command in stale:
        when = verified_at[:10] if verified_at else "never"
        how = "re-run" if command else "re-read"
        out(f"  {when}  {pred}({subject}, {obj})  [{fid}]  {by or '?'}  ({how})")
    if stale:
        out("\n`recheck` re-verifies these: a command-backed fact by running it again,")
        out("a quoted one against its source. What still holds gets a fresh timestamp")
        out("and drops off this list; what does not is reported for retraction.")


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
    who = author(args)
    db.run(
        "?[id, retracted_at, retract_reason, retracted_by] <- $rows :update fact",
        {"rows": [[args.id, now(), args.reason, who]]},
    )
    out(f"retracted {pred}({subject}, {obj})  by {who}")
    out(f"  reason: {args.reason}")
    out("  the fact stays queryable — `show` still returns it")


def cmd_context(args):
    db = connect()
    titles = {r[0]: r[1] for r in query(db, "?[id, title] := *source{id, title}")}
    live = query(
        db,
        "?[id, pred, object, quote, command, source_id, asserted_by, verified_at] := "
        "*fact{id, pred, subject: $s, object, quote, command, source_id, "
        "asserted_by, verified_at, retracted_at: ''}",
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
    for fid, pred, obj, quote, command, sid, by, ver in sorted(live, key=lambda r: r[1]):
        out(f"\n{pred}: {obj}   [{fid}]")
        if command:
            out(f"  $ {command}")
            out(f"  — ran on {ver[:10] if ver else 'unknown date'}, asserted by {by or '?'}")
        else:
            out(f'  "{quote}"')
            out(f"  — {titles.get(sid, sid)}, asserted by {by or '?'}")
    if incoming:
        out("\nreferenced by:")
        for pred, subject in sorted(incoming):
            out(f"  {subject} —{pred}→ {args.subject}")


def cmd_show(args):
    db = connect()
    rs = query(
        db,
        "?[pred, subject, object, quote, command, cwd, expect, host, source_id, "
        "asserted_by, asserted_at, verified_at, retracted_at, retract_reason, "
        "retracted_by] := "
        "*fact{id: $id, pred, subject, object, quote, command, cwd, expect, host, "
        "source_id, asserted_by, asserted_at, verified_at, retracted_at, "
        "retract_reason, retracted_by}",
        {"id": args.id},
    )
    if not rs:
        raise SystemExit(f"no fact with id {args.id}")
    (pred, subject, obj, quote, command, cwd, expect, host, sid,
     by, asserted, verified, retracted, reason, retracted_by) = rs[0]
    out(f"{pred}({subject}, {obj})")
    if command:
        out(f"  command:  $ {command}")
        out(f"  in:       {cwd}" + (f"  on {host}" if host else ""))
        out(f"  output:   {expect!r}")
    else:
        out(f'  quote:    "{quote}"')
        src = query(db, "?[title, uri] := *source{id: $id, title, uri}", {"id": sid})
        title, uri = src[0] if src else (sid, "")
        out(f"  source:   {title}" + (f"  <{uri}>" if uri else ""))
    out(f"  asserted: {asserted} by {by or '?'}")
    out(f"  verified: {verified or 'never'}")
    if retracted:
        out(f"  RETRACTED {retracted} by {retracted_by or '?'}: {reason}")


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
    s.add_argument("--as", dest="as_", default="",
                   help="who is asserting these (else $FACTO_AGENT / $AGENT_MAIL_NAME)")
    s.set_defaults(fn=cmd_load)

    s = sub.add_parser("check", help="run conflicts, gaps, single_source and stale")
    s.add_argument("--days", type=int,
                   default=int(os.environ.get("FACTO_STALE_DAYS", "14")),
                   help="flag facts not verified in this many days (default 14)")
    s.set_defaults(fn=cmd_check)

    sub.add_parser(
        "recheck", help="re-verify every source against the file it came from"
    ).set_defaults(fn=cmd_recheck)

    s = sub.add_parser("retract", help="retract a fact with a reason")
    s.add_argument("id")
    s.add_argument("--reason", required=True)
    s.add_argument("--as", dest="as_", default="",
                   help="who is retracting (else $FACTO_AGENT / $AGENT_MAIL_NAME)")
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
