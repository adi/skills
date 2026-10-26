---
name: watcher
description: Watch something for as long as it takes and get notified each time it happens — log lines, CI status, file changes, a queue, a device coming back. Use when a watch needs to outlive a single notification or a blocking tool call, or must survive across sessions so a later session can pick up events that arrived while nothing was listening. Also use when the user asks to be told every time something happens, to keep an eye on something, to watch a log, to wait for a job, or when missing what arrived between notifications would matter. Events queue on disk rather than being dropped, one watch can be handed from session to session, and a watch that dies says so instead of going quiet.
---

# Watcher

A watch is a command whose stdout lines are events. `watcher` runs it detached
and keeps every line in an append-only spool; `watcher wait` blocks until there
is something to report and then exits.

That split is the whole design. A synchronous tool call cannot notify anything
while it runs, and a notification that fires once cannot keep watching —
producing and waiting have to be different processes. Here the producer runs
forever and the waiter exits the moment it has something, which is exactly the
shape a synchronous Bash tool rewards.

What that buys over ad-hoc waiting:

- **No ceiling tied to one tool call.** The producer runs detached; a single
  `wait` tool call can time out and be re-issued without losing anything.
- **Nothing is lost between notifications.** Events that arrive while no one is
  waiting queue on disk; the next `wait` returns them at once.
- **A watch outlives its session.** The producer is detached in its own session,
  so a new session runs `watcher status`, finds it still running, and carries on
  reading where the last one stopped.
- **A dead watch says so.** `wait` exits 3 with the command's exit code rather
  than going quiet, because a watch that has died looks exactly like a calm one.

## Setup

```bash
python3 <skill-dir>/watcher --help
```

The CLI is Python 3, no dependencies, and is bundled in this skill's directory —
run it from there if it is not on `PATH`. Spools live under
`~/.claude/watchers/<name>/` (`$WATCHER_ROOT` moves them).

## The loop

### 1. Start the watch, once

```bash
<skill-dir>/watcher start deploy -- "tail -n0 -F deploy.log | grep --line-buffered -E 'ERROR|FAILED|Traceback|deployed'"
```

Everything after `--` is the command. Its **stdout lines are the events**; its
stderr is discarded, so merge with `2>&1` if the interesting output goes there.
The working directory is recorded, so a relative path in the command keeps
meaning what it meant.

### 2. Arm a notification

Run this as a **pi Bash tool call with a long timeout (e.g. `timeout: 900`)**:

```bash
<skill-dir>/watcher wait deploy
```

It blocks, prints the new events, and exits — that blocking call returning is
what notifies you, with the lines in hand. Nothing is consumed twice: a cursor
advances only over what was printed.

If the tool call times out first, nothing is lost: events queue in the spool,
and the next `wait` returns whatever is pending immediately. Do not treat the
timeout as a failure — just issue another `wait`. Keep issuing them until you
have what you were watching for.

### 3. Handle what arrived, then arm it again

Re-arm immediately. Events that land in the gap are not lost — they are waiting
for the next `wait` — but the gap is time you are not being told about.

When you are done, `watcher stop deploy`. A stopped watch keeps its history;
`watcher drop deploy` removes it.

## Writing the command

The whole value of a watch is in this line, and two mistakes make it lie.

**Silence is not success.** A filter that matches only the happy path stays
silent through a crash, a hang, or an unexpected exit — and silence is
indistinguishable from "still going". Before starting, ask: *if this thing died
right now, would my filter emit anything?* If not, widen it.

```bash
# Wrong - silent on crash, hang, or any failure
tail -F run.log | grep --line-buffered "step="

# Right - progress and the failures you would act on
tail -F run.log | grep -E --line-buffered "step=|Traceback|FAILED|Killed|OOM"
```

The producer helps a little here: when the command itself exits, that is recorded
and the next `wait` reports it. But a process that hangs without exiting is
invisible unless your filter can see it.

**Every stage must flush per line**, or matches sit in a buffer unseen:
`grep --line-buffered`, `awk '{...; fflush()}'`. `head` cannot flush at all, so
`| head -5` delivers nothing until five matches accumulate. Prefer `tail -F` to
`tail -f` so a rotated or recreated file is picked up rather than watched into
the void.

**Poll intervals**: 30s or more for remote APIs, 0.5–1s for local checks. Handle
transient failures inside the loop (`curl ... || true`) so one bad request does
not end the watch.

## Commands

```bash
watcher start <name> [--cwd DIR] -- <command>   # start a watch
watcher wait <name> [--timeout SECS]            # block until events, print, exit
watcher status [name]                           # what runs, what is queued
watcher log <name> [--tail N]                   # replay history
watcher stop <name>                             # stop, keep history
watcher drop <name>                             # delete a stopped watch
```

`wait` exits **0** with events, **2** on `--timeout`, **3** when the watch is no
longer running, **4** when another waiter already holds it. One consumer per
watch, refused rather than shared: two waiters would each take part of the stream
and neither would see all of it, which is invisible from the outside.

## Picking up a watch from an earlier session

Start a session by asking what is already running:

```bash
watcher status
```

A watch from yesterday is still producing, and its queue is whatever happened
overnight. `watcher wait <name>` hands you those events; `watcher log <name>`
shows them without consuming.

## When this is the wrong tool

**One notification, and you are done** — "tell me when the build finishes",
"wait for the port to open". Use a single synchronous Bash call that exits on
the condition:

```bash
until curl -sf localhost:8080/health >/dev/null; do sleep 1; done
```

Give it a timeout long enough for the condition to plausibly arrive. That is
one process, no spool, no cleanup, and the call returning is your notification.
Reaching for a watch there is strictly more machinery for the same answer.

**A short watch inside one session, with nothing to preserve** — a plain
blocking call covers it. Use a watch when the span is longer than one tool
call's timeout, when it must survive the session, or when missing what arrived
between notifications would matter.
