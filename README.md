# skills

Pi ports of agent-mail and watcher, and their shared local CLIs. `facto` is
unchanged and is not part of this port.

## Pi setup (this branch)

```bash
agent-mail/install.sh
watcher/install.sh
```

These install pi skills under `${PI_CODING_AGENT_DIR:-~/.pi/agent}/skills` and
the mail extension under its `extensions` directory. Existing Claude skills
are not overwritten. The shared CLI and mail roots remain under `~/.claude`.

In pi, run `/reload`, then `/mail pi@ejobs` (register that address first).
Use a different address in each concurrent session. `/mail status` reports
attachment; `/mail off` detaches. Identity is saved per session branch, not in
a global file. Ignore/delete the obsolete `~/.pi/agent/agent-mail-address` file.

The extension uses `fs.watch` on the mailbox's `new/` directory and checks it
at attachment and settlement. It never spawns a CLI watcher or calls
`watcher wait`. It batches mail and triggers a custom-message turn only when
idle, leaving actual messages unread until the agent explicitly reads them.
Filesystem watching targets local filesystems; network filesystem notifications
may not be reliable. Unread mail is rediscovered when a session reattaches.

One `.pi-listener` lock per mailbox prevents duplicate pi listeners. On a crash,
inspect its PID and remove it only after confirming the owner is dead. Existing
Claude producers can coexist; do not let two agents consume the same mailbox.
`agent-mail agents` lists CLI producers, not pi filesystem listeners.

Tests (Node 24 and Python 3):

```bash
node --experimental-strip-types --test agent-mail/extension.test.ts
```

The original CLI background-watch description below describes Claude's
harness, not pi: pi has no background Bash completion notifications. In pi,
use `/mail` for mail; inspect arbitrary watcher logs on request without
blocking the conversation.

## agent-mail — 1:1 mail between local agents

Maildir-style message passing between LLM agents on one machine
(`~/.claude/mail/<name>@<domain>/{tmp,new,cur}`). Delivery is an atomic rename,
so no locking is needed and messages are never torn. Addresses are
`<name>@<domain>`: `<name>` is the role, `<domain>` the group, so several
groups can each run their own `devops` without their mail mixing.

```bash
agent-mail/install.sh
```

Idempotent. Installs the CLI to `~/.claude/mail/bin/agent-mail`, the skill to
`~/.pi/agent/skills/agent-mail/`, installs the pi extension, and adds the CLI to `PATH`. Files are swapped in
by atomic rename, so re-running it will not disturb a long-lived
`agent-mail watch`. Python 3, no dependencies.

Then tell a session "register as devops@platform on agent mail and watch for
messages" — give it both the role and the group, and the skill does the rest.

## watcher — long-lived event watches

A watch is a command whose stdout lines are events. `watcher` runs it detached
and spools every line; `watcher wait <name>` blocks until there is something to
report and then exits — which is what makes the harness notify you, since it
notifies on a background process *exiting*, not while it runs.

```bash
watcher/install.sh
```

Installs the CLI to `~/.claude/watchers/bin/watcher`, the skill to
`~/.pi/agent/skills/watcher/`, and adds the CLI to `PATH`. Python 3, no
dependencies.

```bash
watcher start ci -- "tail -n0 -F ci.log | grep -E --line-buffered 'PASS|FAIL|Traceback'"
watcher wait ci      # run this in the background; it exits when events land
watcher status       # what is running, and what is queued
```

The Monitor tool caps at 30 minutes and dies with the session. A watch has no
ceiling, keeps events that arrive while nobody is listening, outlives the session
that started it, and reports the command's exit rather than going quiet when it
dies. For a single "tell me when X finishes", skip all of this and background a
command that exits on the condition.

## facto — quote-backed fact ledger

Research findings recorded as facts in a Datalog (CozoDB) store that refuses any
claim whose supporting quote is not literally present in the registered source,
detects contradictions between sources, and keeps retracted claims queryable so
belief revision stays auditable.

No install step: the dependency is declared in a PEP 723 block, so
[uv](https://docs.astral.sh/uv/) builds the environment on first run.

```bash
uv run facto/scripts/facto.py init
```

Symlink it where Claude Code will find it:

```bash
ln -s "$PWD/facto" ~/.claude/skills/facto
```

## Layout

One directory per skill, each with a `SKILL.md` at its root. A skill ships
whatever it needs to actually run — scripts, references, an installer — rather
than assuming a tool is already on the machine.
