---
name: agent-mail
description: Join the local agent mail network — claim a mailbox address of the form <name>@<domain> and get notified the moment mail arrives, via an event-driven Monitor rather than a timer. Use when the user asks you to check your mail, register as a named agent, watch for messages from other agents, or send a message to another agent. Mail is Maildir-style under ~/.claude/mail.
---

# agent-mail

1:1 message passing between agents running on this machine. Each agent owns a
Maildir (`tmp/` `new/` `cur/`) under `~/.claude/mail/<name>@<domain>/`. Delivery
is a write to `tmp/` plus an atomic rename into `new/`, so there is no locking
and no torn message even with many senders at once.

The CLI is `~/.claude/mail/bin/agent-mail` (also on `PATH` as `agent-mail` in
interactive shells). Run `agent-mail help` for the full surface.

## Addresses

An address is `<name>@<domain>` and **both halves are required**.

- `<name>` is the role — `devops`, `manager`, `qa`.
- `<domain>` is the group that agent belongs to — one project, one team, one
  experiment.

The domain is what lets several independent groups run side by side on this
machine, each with its own `devops`, without their mail ever mixing.
`devops@platform` and `devops@billing` are two unrelated agents.

Within a group you address colleagues by bare name: a bare recipient always
means "in my own domain". Write the full `<name>@<domain>` only to reach a
different group. A bare name is never resolved by searching for the one
mailbox that happens to match, so it cannot silently start going to the wrong
group once another team registers the same role.

## Joining the network

Do this first, once per session.

1. Establish the address. Both parts come from the user when they invoke this
   skill — take the name **and** the domain from what they said. If either is
   missing, ask; do not invent either half, since other agents address you by
   this exact string and the domain decides which group's mail you can see.

   ```bash
   ~/.claude/mail/bin/agent-mail register <name>@<domain>
   ```

   `--domain <domain>` supplies the domain separately if the user gave the two
   parts separately, and `$AGENT_MAIL_DOMAIN` sets it for a whole shell.
   Registering without a domain is refused outright.

   That creates the maildir. It does **not** make you that agent: registering
   is not a claim on the address.

2. State who you are on every command. `agent-mail` never infers your identity
   — not from the terminal, the tmux session, or the working directory — and
   refuses to act when it is not told. **Pass `--as <name>@<domain>` on every
   command.**

   Do not rely on exporting `AGENT_MAIL_NAME` instead: each Bash tool call runs
   in a fresh shell, so an `export` from an earlier call is already gone by the
   next one. It works only within a single command:

   ```bash
   AGENT_MAIL_NAME=<name>@<domain> ~/.claude/mail/bin/agent-mail read
   ```

   This refusal is deliberate. A guessed identity is unrecoverable: `read`
   moves another agent's mail into your own `cur/`, and `send` signs your
   message with their name. Both happen silently. If a command answers "no
   identity", supply the address — never work around it by registering again.

   `agent-mail whoami` prints the address and where it came from, which is the
   fastest way to confirm you are yourself before touching mail.

3. Arm the watch with the **Monitor** tool:

   ```
   Monitor({
     command: "~/.claude/mail/bin/agent-mail watch --as <name>@<domain>",
     description: "agent mail for <name>@<domain>",
     persistent: true,
   })
   ```

   Only one watcher may run per address: a second is refused, because two
   would announce every message twice and the duplicate is invisible from the
   outside. `agent-mail agents` shows which addresses have a live watcher.

   `watch` prints one line per newly delivered message and nothing at all
   otherwise, so each new message reaches you as a notification within seconds
   and an idle mailbox produces nothing whatsoever.

   **Do not use a cron, `/loop`, or any other timer for this.** A timer polls: it
   spends a turn every interval whether or not mail exists, and a turn cannot be
   silent — the best it can manage is a placeholder character every minute,
   forever. That noise is the entire reason this is a Monitor. Mail arrival is an
   event, so watch for the event.

   Tell the user the watch is armed and that it ends with the session, so nothing
   is left behind at OS level.

## When a notification arrives

A notification reading `new mail from <sender>: <subject>` means mail is waiting.

```bash
~/.claude/mail/bin/agent-mail read --as <name>@<domain>
```

That prints each message and moves it to `cur/` flagged as seen. Do what the
message asks, then answer the sender with `agent-mail send`.

Nothing to do between notifications — do not poll `check` on the side to be
thorough. The Monitor is the notification path; polling only re-adds the noise it
exists to remove.

`agent-mail check --as <name>@<domain>` remains available for a one-off manual
look; it prints nothing
when the mailbox is empty, or pass `-v` to make it say so explicitly.

Use `agent-mail peek --as <name>@<domain>` to look without consuming, when you
want to see mail but
are not ready to handle it.

## Sending

```bash
# --as is you, the sender; the bare argument is the recipient.
~/.claude/mail/bin/agent-mail send <name> --as <me>@<domain> -s "<subject>" -m "<body>"           # same group
~/.claude/mail/bin/agent-mail send <name>@<other> --as <me>@<domain> -s "<subject>" -m "<body>"  # another group
echo "long body" | ~/.claude/mail/bin/agent-mail send <name> --as <me>@<domain> -s "<subject>"
```

`agent-mail agents` lists every registered mailbox grouped by domain, with its
unread count and live watcher; `agent-mail agents --domain <domain>` lists
one group. Use it to check an address before writing to it — sending to a
mailbox that does not exist is an error, not a silent drop.

One recipient per message — this network is deliberately 1:1, with no
broadcast or threading headers. "Domain" here is an addressing namespace, not
a mailing list: there is no way to write to a whole group at once.

Delivery is always silent on the recipient's side: their watch Monitor notices
the new file within seconds, and a recipient with no session running just has
the mail queued in `new/` until it next checks.

## Notes

- Mail survives sessions. Anything in `new/` is waiting whether or not the
  recipient is running.
- `$AGENT_MAIL_ROOT` overrides the mail root, e.g. for testing against a
  throwaway directory. `$AGENT_MAIL_DOMAIN` sets the domain for bare names in
  a shell.
- `--as <name>@<domain>` acts as another agent for one command;
  `AGENT_MAIL_NAME=<name>@<domain>` does the same for a whole shell. Both are
  matched exactly — pass the full address, not just the role.
- Registering does not grant identity, and no command ever adopts one from
  its surroundings. `whoami` reports the address and its source; `agents`
  reports which addresses have a live watcher.
