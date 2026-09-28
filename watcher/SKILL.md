---
name: watcher
description: Watch something for as long as it takes and get notified each time it happens — log lines, CI status, file changes, a queue, a device coming back. Use when a watch needs to outlive a single notification or a 30-minute ceiling: the Monitor tool caps out at 30 minutes and dies with the session, so anything longer has to be rearmed by hand and loses whatever arrived in between. Also use when the user asks to be told every time something happens, to keep an eye on something, to watch a log, to wait for a job, or when a watch should survive across sessions so a later session can pick up events that arrived while nothing was listening. Events queue on disk rather than being dropped, one watch can be handed from session to session, and a watch that dies says so instead of going quiet.
---

# Watcher

A watch is a command whose stdout lines are events. `watcher` runs it detached
and keeps every line in an append-only spool; `watcher wait` blocks until there
is something to report and then exits.

That split is the whole design. The harness notifies an agent when a background
process **exits**, not while it runs — so a watch that never exits can never
notify, and a notification that fires once cannot keep watching. Producing and
waiting have to be different processes. Here the producer runs forever and the
waiter exits the moment it has something, which is exactly the shape the harness
rewards.

What that buys over arming a Monitor:

- **No ceiling.** Monitor stops at 30 minutes, and with it the watch.
- **Nothing is lost between notifications.** Events that arrive while no one is
  waiting queue on disk; the next `wait` returns them at once.
- **A watch outlives its session.** The producer is detached in its own session,
  so a new session runs `watcher status`, finds it still running, and carries on
  reading where the last one stopped.
- **A dead watch says so.** `wait` exits 3 with the command's exit code rather
  than going quiet, because a watch that has died looks exactly like a calm one.

## Setup

```bash
watcher/install.sh
```

Installs the CLI to `~/.claude/watchers/bin/watcher` and puts it on `PATH`.
Python 3, no dependencies. Spools live under `~/.claude/watchers/<name>/`
(`$WATCHER_ROOT` moves them).

## The loop

### 1. Start the watch, once

```bash
watcher start deploy -- "tail -n0 -F deploy.log | grep --line-buffered -E 'ERROR|FAILED|Traceback|deployed'"
```

Everything after `--` is the command. Its **stdout lines are the events**; its
stderr is discarded, so merge with `2>&1` if the interesting output goes there.
The working directory is recorded, so a relative path in the command keeps
meaning what it meant.

### 2. Arm a notification

Run this with the **Bash tool, `run_in_background: true`**:

```bash
watcher wait deploy
```

It blocks, prints the new events, and exits — and that exit is what re-invokes
you, with the lines in hand. Nothing is consumed twice: a cursor advances only
over what was printed.

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
"wait for the port to open". Use the Bash tool with `run_in_background: true`
and a command that exits on the condition:

```bash
until curl -sf localhost:8080/health >/dev/null; do sleep 1; done
```

That is one process, no spool, no cleanup, and you are notified when it exits.
Reaching for a watch there is strictly more machinery for the same answer.

**A short watch inside one session** — under 30 minutes, nothing to preserve
afterwards. The Monitor tool does that with no files on disk and no `stop` to
remember. Use a watch when the span is longer than that, when it must survive the
session, or when missing what arrived between notifications would matter.
