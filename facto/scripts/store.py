"""CozoDB-backed fact store for the facto skill.

Everything that touches the database lives here; facto.py is the CLI over it.
Facts are append-only: retraction stamps a timestamp and reason, so the record
of what was believed when survives every revision.
"""

import os
import hashlib
import socket
import subprocess
from datetime import datetime, timezone


def store_dir():
    return os.path.abspath(os.environ.get("FACTO_DIR", ".facto"))


def sources_dir():
    return os.path.join(store_dir(), "sources")


def db_path():
    return os.path.join(store_dir(), "store.db")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def normalize(text):
    """Whitespace-insensitive form used for quote verification."""
    return " ".join(text.split())


# --- schema -----------------------------------------------------------------

SCHEMA = [
    """
    :create source {
        id: String
        =>
        title: String,
        uri: String,
        path: String,
        origin: String,
        sha: String,
        added_at: String,
        verified_at: String,
    }
    """,
    """
    :create predicate {
        name: String
        =>
        description: String,
        functional: Bool,
        object_type: String,
        declared_at: String,
    }
    """,
    """
    :create proposal {
        name: String
        =>
        rationale: String,
        quote: String,
        source_id: String,
        proposed_at: String,
    }
    """,
    """
    :create fact {
        id: String
        =>
        pred: String,
        subject: String,
        object: String,
        source_id: String,
        quote: String,
        command: String,
        cwd: String,
        expect: String,
        host: String,
        asserted_by: String,
        asserted_at: String,
        verified_at: String,
        retracted_at: String,
        retract_reason: String,
        retracted_by: String,
    }
    """,
]

# --- rules ------------------------------------------------------------------
# Add new rules here and wire them into `check` in facto.py so they run as part
# of the routine pass rather than only when someone remembers to ask.

# Who asserted each side is part of the answer, not decoration: a functional
# conflict between two agents is the case this store exists to settle.
CONFLICTS = """
?[pred, subject, o1, id1, q1, c1, s1, b1, o2, id2, q2, c2, s2, b2] :=
    *predicate{name: pred, functional: true},
    *fact{id: id1, pred, subject, object: o1, quote: q1, command: c1,
          source_id: s1, asserted_by: b1, retracted_at: ''},
    *fact{id: id2, pred, subject, object: o2, quote: q2, command: c2,
          source_id: s2, asserted_by: b2, retracted_at: ''},
    o1 < o2
"""

GAPS = """
described[s] := *fact{subject: s, retracted_at: ''}
?[entity, pred, subject] :=
    *predicate{name: pred, object_type: 'entity'},
    *fact{pred, subject, object: entity, retracted_at: ''},
    not described[entity]
"""

SINGLE_SOURCE = """
srcs[subject, pred, source_id] := *fact{pred, subject, source_id, retracted_at: ''}
counted[subject, pred, count(source_id)] := srcs[subject, pred, source_id]
?[subject, pred, n] := counted[subject, pred, n], n == 1
"""

# Staleness has to arrive without being asked for, or it arrives too late: a fact
# that was true when written and never re-read is the failure mode that costs
# weeks. `verified_at` is stamped by load and by recheck, so this asks the
# question "when did anyone last see this hold?" rather than "is it plausible?".
#
# An empty verified_at sorts before any cutoff, so a fact from before this
# existed reads as stale, which is the honest answer about it.
STALE = """
?[id, pred, subject, object, verified_at, asserted_by, source_id, command] :=
    *fact{id, pred, subject, object, verified_at, asserted_by, source_id, command,
          retracted_at: ''},
    verified_at < $cutoff
    :order verified_at
"""


# --- connection -------------------------------------------------------------

def connect(create=False):
    try:
        from pycozo.client import Client
    except ImportError:
        raise SystemExit(
            "pycozo is missing, which means this was not run through uv.\n"
            "Use: uv run scripts/facto.py <command>   (or ./scripts/facto.py)\n"
            "uv reads the dependency block at the top of facto.py and builds the\n"
            "environment itself; install it from https://docs.astral.sh/uv/ if needed."
        )
    path = db_path()
    if not create and not os.path.exists(path):
        raise SystemExit(
            f"no store at {path} — run `uv run scripts/facto.py init` first"
        )
    os.makedirs(store_dir(), exist_ok=True)
    # dataframe=False keeps results as plain dicts and stops pycozo printing a
    # pandas-import traceback on installs without pandas.
    try:
        db = Client("sqlite", path, dataframe=False)
    except TypeError:
        db = Client("sqlite", path)
    if not create:
        migrate(db)
    return db


def rows(result):
    """pycozo returns a dict or a DataFrame depending on whether pandas is
    installed. Normalize to a list of lists."""
    if isinstance(result, dict):
        return result.get("rows", [])
    return result.values.tolist()


def query(db, script, params=None):
    return rows(db.run(script, params or {}))


SOURCE_FIELDS = "id, title, uri, path, origin, sha, added_at"


def migrate(db):
    """Bring an older store up to the current schema.

    Cozo relations have a fixed column set, so each step rebuilds a relation
    rather than altering it. Every step is guarded by a probe query and adds
    empty values, which the rules read as "unknown" rather than "fine": a fact
    with no verified_at reads as stale, a source with no origin as unverifiable.
    """
    # `origin` on source: added when recheck arrived.
    try:
        db.run("?[origin] := *source{origin}")
    except Exception:
        db.run("""
            :create source_v2 {
                id: String
                =>
                title: String, uri: String, path: String, origin: String,
                sha: String, added_at: String,
            }
        """)
        db.run(
            "?[id, title, uri, path, origin, sha, added_at] := "
            "*source{id, title, uri, path, sha, added_at}, origin = '' \n"
            ":put source_v2"
        )
        db.run("::remove source")
        db.run("::rename source_v2 -> source")

    # `verified_at` on source: when a recheck last found it unchanged.
    try:
        db.run("?[verified_at] := *source{verified_at}")
    except Exception:
        db.run("""
            :create source_v3 {
                id: String
                =>
                title: String, uri: String, path: String, origin: String,
                sha: String, added_at: String, verified_at: String,
            }
        """)
        db.run(
            "?[id, title, uri, path, origin, sha, added_at, verified_at] := "
            "*source{id, title, uri, path, origin, sha, added_at}, verified_at = '' \n"
            ":put source_v3"
        )
        db.run("::remove source")
        db.run("::rename source_v3 -> source")

    # Command evidence, authorship and verification time on fact.
    try:
        db.run("?[command] := *fact{command}")
    except Exception:
        db.run("""
            :create fact_v2 {
                id: String
                =>
                pred: String, subject: String, object: String, source_id: String,
                quote: String, command: String, cwd: String, expect: String,
                host: String, asserted_by: String, asserted_at: String,
                verified_at: String, retracted_at: String, retract_reason: String,
                retracted_by: String,
            }
        """)
        db.run(
            "?[id, pred, subject, object, source_id, quote, command, cwd, expect, "
            "host, asserted_by, asserted_at, verified_at, retracted_at, "
            "retract_reason, retracted_by] := "
            "*fact{id, pred, subject, object, source_id, quote, asserted_at, "
            "retracted_at, retract_reason}, "
            "command = '', cwd = '', expect = '', host = '', asserted_by = '', "
            "verified_at = '', retracted_by = '' \n"
            ":put fact_v2"
        )
        db.run("::remove fact")
        db.run("::rename fact_v2 -> fact")


# How long a command may run before its evidence is treated as unavailable. A
# command that hangs must not hang the load.
COMMAND_TIMEOUT = int(os.environ.get("FACTO_COMMAND_TIMEOUT", "30"))


def run_command(command, cwd):
    """Run a fact's command and return (ok, output).

    The command is the evidence, so it has to actually run - here, and again on
    every recheck. That is the whole point: a count is not checkable by reading a
    quote, but it is checkable by re-running the thing that produced it.

    Set FACTO_RUN_COMMANDS=0 to refuse to run any: the store then rejects
    command-backed facts rather than storing evidence it never verified.
    """
    if os.environ.get("FACTO_RUN_COMMANDS", "1") != "1":
        return False, "running commands is disabled (FACTO_RUN_COMMANDS=0)"
    cwd = cwd or os.getcwd()
    if not os.path.isdir(cwd):
        return False, f"no such directory: {cwd}"
    try:
        p = subprocess.run(
            ["bash", "-o", "pipefail", "-c", command],
            cwd=cwd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {COMMAND_TIMEOUT}s"
    except OSError as e:
        return False, str(e)
    out = p.stdout.strip()
    if p.returncode != 0:
        err = (p.stderr.strip().splitlines() or [""])[0]
        return False, f"exit {p.returncode}: {err or out}"
    return True, out


def init(db):
    for stmt in SCHEMA:
        db.run(stmt)
    os.makedirs(sources_dir(), exist_ok=True)
