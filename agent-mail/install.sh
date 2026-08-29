#!/bin/bash
# Installer for agent-mail: the CLI and its Claude Code skill.
# Safe to re-run; each step is idempotent.
#
# This repo is agent-mail's home. Run this from a clone:
#
#     agent-mail/install.sh
#
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
BASHRC=$HOME/.bashrc

echo "== agent-mail =="
command -v python3 >/dev/null || echo "warning: python3 not found; the CLI needs it" >&2

# Swap each file in by atomic rename instead of writing over it in place.
# `agent-mail watch` processes are long-lived and read their own file lazily,
# so truncating one underneath a running watcher can make it execute garbage;
# rename() leaves those processes on the old inode until they exit.
_swap_in() {   # _swap_in <mode> <src> <dst>
  install -m "$1" "$2" "$3.new.$$"
  mv -f "$3.new.$$" "$3"
}

mkdir -p "$HOME/.claude/mail/bin" "$HOME/.claude/skills/agent-mail"
_swap_in 0755 "$HERE/agent-mail" "$HOME/.claude/mail/bin/agent-mail"
_swap_in 0644 "$HERE/SKILL.md" "$HOME/.claude/skills/agent-mail/SKILL.md"
echo "installed CLI to ~/.claude/mail/bin/agent-mail"
echo "installed skill to ~/.claude/skills/agent-mail/SKILL.md"

if grep -qF '.claude/mail/bin' "$BASHRC" 2>/dev/null; then
  echo "PATH entry already in $BASHRC"
else
  {
    echo ""
    echo "# agent-mail: put the CLI on PATH"
    echo 'case ":$PATH:" in'
    echo '  *":$HOME/.claude/mail/bin:"*) ;;'
    echo '  *) PATH="$PATH:$HOME/.claude/mail/bin" ;;'
    echo 'esac'
  } >> "$BASHRC"
  echo "added ~/.claude/mail/bin to PATH in $BASHRC"
fi

echo ""
echo "Done. Open a new shell (or 'source ~/.bashrc') to pick up the changes."
