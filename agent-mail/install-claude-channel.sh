#!/usr/bin/env bash
# Write the MCP config that gives one Claude Code session the agent-mail channel for one address.
# Per session, not `claude mcp add --scope user`: a user-scope server starts in every session,
# all with the same address, and only the first could take the mailbox's channel lock.
set -euo pipefail
address="${1:?usage: install-claude-channel.sh <name@domain>}"
[[ "$address" =~ ^[A-Za-z0-9_-][A-Za-z0-9._-]*@[A-Za-z0-9_-][A-Za-z0-9._-]*$ ]] || { echo "bad address: $address" >&2; exit 2; }
source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mail_root="${AGENT_MAIL_ROOT:-$HOME/.claude/mail}"
[[ -d "$mail_root/$address/new" ]] || { echo "no mailbox for $address under $mail_root; register it first" >&2; exit 1; }
config_dir="${CLAUDE_CHANNEL_CONFIG_DIR:-$HOME/.claude/agent-mail-channel}"
mkdir -p "$config_dir"
config="$config_dir/$address.json"
env_json='{}'
[[ -n "${AGENT_MAIL_ROOT:-}" ]] && env_json="{\"AGENT_MAIL_ROOT\": \"$AGENT_MAIL_ROOT\"}"
cat > "$config" <<JSON
{"mcpServers": {"agent-mail": {"command": "$(command -v node)", "args": ["$source_dir/claude-channel.mjs", "$address"], "env": $env_json}}}
JSON
printf 'Wrote %s\nStart the session with:\n  claude --mcp-config %s --dangerously-load-development-channels server:agent-mail\nthen press Enter on the warning screen (option 1, "I am using this for local development", is preselected).\n' "$config" "$config"
