#!/bin/bash
# Installer for watcher: the CLI and its Claude Code skill.
# Safe to re-run; each step is idempotent.
#
# This repo is watcher's home. Run this from a clone:
#
#     watcher/install.sh
#
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
BIN_DIR="$HOME/.claude/watchers/bin"
BASHRC=$HOME/.bashrc

echo "== watcher =="
command -v python3 >/dev/null || echo "warning: python3 not found; the CLI needs it" >&2

# Swap each file in by atomic rename instead of writing over it in place. A
# producer started by an older copy keeps running from the old inode until it
# exits, rather than having its file changed underneath it.
_swap_in() {   # _swap_in <mode> <src> <dst>
  install -m "$1" "$2" "$3.new.$$"
  mv -f "$3.new.$$" "$3"
}

mkdir -p "$BIN_DIR" "$HOME/.claude/skills/watcher"
_swap_in 0755 "$HERE/watcher" "$BIN_DIR/watcher"
_swap_in 0644 "$HERE/SKILL.md" "$HOME/.claude/skills/watcher/SKILL.md"
echo "installed CLI to ~/.claude/watchers/bin/watcher"
echo "installed skill to ~/.claude/skills/watcher/SKILL.md"

if grep -qF '.claude/watchers/bin' "$BASHRC" 2>/dev/null; then
  echo "PATH entry already in $BASHRC"
else
  # Name the file the user's own shell actually reads: on macOS that is zsh, and
  # pointing a Mac user at ~/.bashrc is advice that silently does nothing.
  rc="$BASHRC"
  case "$(basename "${SHELL:-bash}")" in
    zsh)  rc="$HOME/.zshrc" ;;
    fish) rc="" ;;
  esac
  if [[ -z $rc ]]; then
    echo "note: add to your fish config:  fish_add_path \"\$HOME/.claude/watchers/bin\"" >&2
  else
    {
      echo ""
      echo "# watcher: put the CLI on PATH"
      echo 'case ":$PATH:" in'
      echo '  *":$HOME/.claude/watchers/bin:"*) ;;'
      echo '  *) PATH="$PATH:$HOME/.claude/watchers/bin" ;;'
      echo 'esac'
    } >> "$rc"
    echo "added ~/.claude/watchers/bin to PATH in $rc"
  fi
fi

echo ""
echo "Done. Open a new shell (or 'source ~/.bashrc') to pick up the changes."
