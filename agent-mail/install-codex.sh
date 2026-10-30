#!/usr/bin/env bash
# Install Codex mail tools from their canonical agent-mail repository.
set -euo pipefail
AGENT_MAIL_SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_MAIL_NODE="$(command -v node)"
AGENT_MAIL_CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
AGENT_MAIL_BIN="${AGENT_MAIL_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$AGENT_MAIL_BIN" "$AGENT_MAIL_CODEX_HOME/skills"
chmod +x "$AGENT_MAIL_SOURCE/codex.mjs"
ln -sfn "$AGENT_MAIL_SOURCE/codex.mjs" "$AGENT_MAIL_BIN/codex-agent-mail"
if [[ -e "$AGENT_MAIL_CODEX_HOME/skills/agent-mail" && ! -L "$AGENT_MAIL_CODEX_HOME/skills/agent-mail" ]]; then
  echo 'Existing Codex agent-mail skill is a directory; preserving it.' >&2
else
  ln -sfn "$AGENT_MAIL_SOURCE" "$AGENT_MAIL_CODEX_HOME/skills/agent-mail"
fi
codex mcp add agent-mail --env "AGENT_MAIL_CLI=$AGENT_MAIL_SOURCE/agent-mail" -- "$AGENT_MAIL_NODE" "$AGENT_MAIL_SOURCE/codex.mjs" --mcp
printf 'Installed agent-mail MCP, Codex skill, and %s/codex-agent-mail.\n' "$AGENT_MAIL_BIN"
