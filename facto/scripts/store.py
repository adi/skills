"""CozoDB-backed fact store for the facto skill.

Everything that touches the database lives here; facto.py is the CLI over it.
Facts are append-only: retraction stamps a timestamp and reason, so the record
of what was believed when survives every revision.
"""

import os
import hashlib
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
        asserted_at: String,
        retracted_at: String,
        retract_reason: String,
    }
    """,
]

# --- rules ------------------------------------------------------------------
# Add new rules here and wire them into `check` in facto.py so they run as part
# of the routine pass rather than only when someone remembers to ask.

CONFLICTS = """
?[pred, subject, o1, id1, q1, s1, o2, id2, q2, s2] :=
    *predicate{name: pred, functional: true},
    *fact{id: id1, pred, subject, object: o1, quote: q1, source_id: s1, retracted_at: ''},
    *fact{id: id2, pred, subject, object: o2, quote: q2, source_id: s2, retracted_at: ''},
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
    """Add `origin` to stores created before recheck existed.

    Cozo relations have a fixed column set, so this rebuilds `source` rather
    than altering it. Pre-existing rows get an empty origin, which recheck
    reports as unverifiable instead of silently passing.
    """
    try:
        db.run("?[origin] := *source{origin}")
        return
    except Exception:
        pass
    db.run("""
        :create source_v2 {
            id: String
            =>
            title: String,
            uri: String,
            path: String,
            origin: String,
            sha: String,
            added_at: String,
        }
    """)
    db.run(
        "?[id, title, uri, path, origin, sha, added_at] := "
        "*source{id, title, uri, path, sha, added_at}, origin = '' \n"
        ":put source_v2"
    )
    db.run("::remove source")
    db.run("::rename source_v2 -> source")


def init(db):
    for stmt in SCHEMA:
        db.run(stmt)
    os.makedirs(sources_dir(), exist_ok=True)
