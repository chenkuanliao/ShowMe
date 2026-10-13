#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BIN_DIR=${HOME}/.local/bin
SHARE_DIR=${HOME}/.local/share/showme
MANIFEST=$SHARE_DIR/install-manifest
CODEX_SKILL=""
CODEX_FILE=""
CLAUDE_SKILL=""
CLAUDE_FILE=""
SERVER=""
TOKEN=""
AGENT=""
CODEX_DEFAULT=ask
CLAUDE_DEFAULT=ask
UPGRADE=no

usage() {
  echo "Usage: ./install.sh [--agent codex|claude|both] [--server URL] [--token TOKEN] [--codex-default|--no-codex-default] [--claude-default|--no-claude-default]"
  echo "       ./install.sh --upgrade"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --agent) AGENT=${2:?Missing agent}; shift 2 ;;
    --server) SERVER=${2:?Missing URL}; shift 2 ;;
    --token) TOKEN=${2:?Missing token}; shift 2 ;;
    --codex-default) CODEX_DEFAULT=yes; shift ;;
    --no-codex-default) CODEX_DEFAULT=no; shift ;;
    --claude-default) CLAUDE_DEFAULT=yes; shift ;;
    --no-claude-default) CLAUDE_DEFAULT=no; shift ;;
    --upgrade) UPGRADE=yes; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$AGENT" in ""|codex|claude|both) ;; *) echo "Invalid agent: $AGENT (choose codex, claude, or both)" >&2; exit 2 ;; esac
if [ "$UPGRADE" = yes ] && { [ -n "$SERVER" ] || [ -n "$TOKEN" ] || [ -n "$AGENT" ] || [ "$CODEX_DEFAULT" != ask ] || [ "$CLAUDE_DEFAULT" != ask ]; }; then
  echo "--upgrade cannot be combined with configuration options" >&2
  exit 2
fi
if [ "$UPGRADE" = yes ] && [ ! -f "$MANIFEST" ]; then
  echo "ShowMe is not installed as a managed installation; run ./install.sh without --upgrade first." >&2
  exit 1
fi

# The first four lines retain the legacy manifest format; Claude uses lines 5-6.
# Preserve installed paths, including custom instruction paths, across upgrades.
if [ -f "$MANIFEST" ]; then
  CODEX_SKILL=$(sed -n '3p' "$MANIFEST")
  CODEX_FILE=$(sed -n '4p' "$MANIFEST")
  CLAUDE_SKILL=$(sed -n '5p' "$MANIFEST")
  CLAUDE_FILE=$(sed -n '6p' "$MANIFEST")
fi
if [ "$UPGRADE" = no ]; then
  AGENT=${AGENT:-codex}
  if [ "$AGENT" = claude ] && [ "$CODEX_DEFAULT" != ask ]; then
    echo "Codex preference flags require --agent codex or both" >&2; exit 2
  fi
  if [ "$AGENT" = codex ] && [ "$CLAUDE_DEFAULT" != ask ]; then
    echo "Claude preference flags require --agent claude or both" >&2; exit 2
  fi
  case "$AGENT" in codex|both)
    CODEX_SKILL=${CODEX_SKILL:-${HOME}/.agents/skills/showme-publish}
    CODEX_FILE=${CODEX_FILE:-${CODEX_HOME:-${HOME}/.codex}/AGENTS.md}
    ;; esac
  case "$AGENT" in claude|both)
    CLAUDE_SKILL=${CLAUDE_SKILL:-${HOME}/.claude/skills/showme-publish}
    CLAUDE_FILE=${CLAUDE_FILE:-${HOME}/.claude/CLAUDE.md}
    ;; esac
fi

# Check every destination before changing an existing installation.
if [ -e "$BIN_DIR/showme" ] && [ ! -f "$MANIFEST" ]; then
  echo "Refusing to overwrite an unmanaged command at $BIN_DIR/showme" >&2; exit 1
fi
for skill_dir in "$CODEX_SKILL" "$CLAUDE_SKILL"; do
  [ -n "$skill_dir" ] || continue
  if [ -e "$skill_dir" ] && [ ! -f "$skill_dir/.showme-managed" ]; then
    echo "Refusing to overwrite an unmanaged skill at $skill_dir" >&2; exit 1
  fi
done
mkdir -p "$BIN_DIR" "$SHARE_DIR"
cp "$REPO_DIR/cli/showme.py" "$BIN_DIR/showme"
chmod 755 "$BIN_DIR/showme"
cp "$REPO_DIR/scripts/uninstall.sh" "$SHARE_DIR/uninstall.sh"
chmod 755 "$SHARE_DIR/uninstall.sh"
for skill_dir in "$CODEX_SKILL" "$CLAUDE_SKILL"; do
  [ -n "$skill_dir" ] || continue
  mkdir -p "$(dirname "$skill_dir")"
  rm -rf "$skill_dir"
  mkdir -p "$skill_dir"
  cp "$REPO_DIR/skill/showme-publish/SKILL.md" "$skill_dir/SKILL.md"
  if [ "$skill_dir" = "$CODEX_SKILL" ]; then
    cp -R "$REPO_DIR/skill/showme-publish/agents" "$skill_dir/agents"
  fi
  touch "$skill_dir/.showme-managed"
done
printf '%s\n' "$BIN_DIR/showme" "$SHARE_DIR" "$CODEX_SKILL" "$CODEX_FILE" "$CLAUDE_SKILL" "$CLAUDE_FILE" > "$MANIFEST"

case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *) echo "Add this directory to PATH if your shell does not already include it: $BIN_DIR" ;;
esac

set_preference() {
  preference=$1
  instruction_file=$2
  agent_name=$3
  if [ "$preference" = ask ]; then
    printf "Make ShowMe the default for %s-generated webpages? [Y/n] " "$agent_name"
    read -r answer
    case "$answer" in n|N|no|NO) preference=no ;; *) preference=yes ;; esac
  fi
  if [ "$preference" = no ] && [ ! -f "$instruction_file" ]; then
    return
  fi
  mkdir -p "$(dirname "$instruction_file")"
  touch "$instruction_file"
  preference_tmp=$(mktemp "${instruction_file}.showme.XXXXXX")
  awk '
    /^<!-- showme:start -->$/ { managed = 1; next }
    /^<!-- showme:end -->$/ { managed = 0; next }
    !managed { print }
  ' "$instruction_file" > "$preference_tmp"
  if [ "$preference" = yes ]; then
    cat >> "$preference_tmp" <<'PREFERENCE'

<!-- showme:start -->
## Generated webpages

For any HTML report, dashboard, visual summary, or webpage intended for me to view, use the showme-publish skill, publish it automatically, and always return the clickable ShowMe URL without waiting for me to ask for the link. If I explicitly invoke the skill, publishing and returning the link are required parts of completing the task unless I explicitly request local-only output.
<!-- showme:end -->
PREFERENCE
  fi
  mv "$preference_tmp" "$instruction_file"
}

if [ "$UPGRADE" = no ]; then
  case "$AGENT" in codex|both) set_preference "$CODEX_DEFAULT" "$CODEX_FILE" Codex ;; esac
  case "$AGENT" in claude|both) set_preference "$CLAUDE_DEFAULT" "$CLAUDE_FILE" 'Claude Code' ;; esac
fi

if [ "$UPGRADE" = yes ]; then
  "$BIN_DIR/showme" doctor
elif [ -n "$SERVER" ] && [ -n "$TOKEN" ]; then
  "$BIN_DIR/showme" configure --server "$SERVER" --token "$TOKEN"
elif [ -n "$SERVER" ]; then
  "$BIN_DIR/showme" configure --server "$SERVER"
elif [ -n "$TOKEN" ]; then
  "$BIN_DIR/showme" configure --token "$TOKEN"
else
  "$BIN_DIR/showme" configure
fi

echo ""
if [ "$UPGRADE" = yes ]; then
  echo "ShowMe upgraded. Restart your installed coding agents to load the updated skill."
else
  echo "ShowMe installed. Restart your selected coding agents if the skill is not immediately visible."
fi
echo "Try: showme doctor"
