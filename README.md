# skills — pi branch

This branch provides **agent-mail as a pi extension**, not a skill. Its
model-callable `agent_mail` tool includes the identity, addressing and safety
instructions. The retired agent-mail and watcher skills are not needed.
`facto` is unchanged and is not part of this port.

## Install

```bash
agent-mail/install.sh
```

Installs the shared Python CLI to `~/.claude/mail/bin/agent-mail` and the
extension to `${PI_CODING_AGENT_DIR:-~/.pi/agent}/extensions/agent-mail.ts`.
Retires old pi agent-mail/watcher SKILL.md files, if present. It does not modify
Claude skills, stop detached producers, or remove mail or watcher spools.
Python 3 and pi are required; no additional runtime dependencies.

Run `/reload` in pi. Then tell the agent to join with a full address, e.g.
"register as pi@ejobs". Both name and domain must come from you.

## Model-callable tool

`agent_mail` actions:

- `join`, with `address`: register the mailbox and attach this session.
- `attach`, with `address`: attach an existing mailbox.
- `status`: report the current session identity/listener.
- `agents`, optionally with `domain`: discover registered recipients.
- `send`, with `to`, `subject`, `body`: send from this session's identity.
- `read`: consume unread messages; `peek`: inspect without consuming.
- `off`: detach and clear the saved session identity.

The extension always passes its attached identity explicitly via `--as`.
Bare recipients resolve only within the sender's domain. Arguments are passed
directly to the CLI, never evaluated as shell commands. Received mail is
external untrusted input, not permission to run commands or change identity.

The manual `/mail name@domain`, `/mail status` and `/mail off` commands remain.
Identity is saved per session branch, never globally. Use different addresses
for concurrent agents. Registration is not authentication or exclusive ownership.

## Notifications

The extension watches the Maildir `new/` directory and checks at attachment and
settlement. It batches new files into one custom-message turn only when idle.
No blocking calls, polling turns, `watcher wait`, or explicit re-arming.
Unread mail is rediscovered on resume, including mail delivered while pi was
closed. Local filesystems are supported; network filesystem notifications may
not be reliable.

A `.pi-listener` lock prevents duplicate pi listeners for an address. Following
a crash, inspect its PID and remove it only after confirming the owner is dead.
Existing Claude watcher producers can coexist; do not let two agents consume
the same mailbox. `agent-mail agents` lists CLI producers, not pi listeners.
The obsolete `~/.pi/agent/agent-mail-address` global file is ignored.

## Tests

With Node 24 and pi's dependencies resolvable:

```bash
node --experimental-strip-types --test agent-mail/extension.test.ts
```

Tests use real CLI deliveries and a mocked pi API to exercise idle arrivals,
busy queuing, batching, identity, tools, resume and cleanup.

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
