#!/bin/sh
set -eu

BIN_PATH=${HOME}/.local/bin/showme
SHARE_DIR=${HOME}/.local/share/showme
SKILL_DIR=${HOME}/.agents/skills/showme-publish
AGENTS_FILE=${CODEX_HOME:-${HOME}/.codex}/AGENTS.md
CONFIG_DIR=${XDG_CONFIG_HOME:-${HOME}/.config}/showme
MANIFEST=${SHARE_DIR}/install-manifest
REMOVE_CONFIG=ask

while [ "$#" -gt 0 ]; do
  case "$1" in
    --all) REMOVE_CONFIG=yes; shift ;;
    --keep-config) REMOVE_CONFIG=no; shift ;;
    -h|--help)
      echo "Usage: showme uninstall [--all|--keep-config]"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [ "$REMOVE_CONFIG" = "ask" ]; then
  printf "Also remove the saved server URL and API token? [y/N] "
  read -r answer
  case "$answer" in y|Y|yes|YES) REMOVE_CONFIG=yes ;; *) REMOVE_CONFIG=no ;; esac
fi

if [ -f "$MANIFEST" ]; then
  rm -f "$BIN_PATH"
  if [ -f "$SKILL_DIR/.showme-managed" ]; then
    rm -rf "$SKILL_DIR"
  fi
else
  echo "ShowMe installation manifest is missing; refusing to remove CLI or skill automatically." >&2
fi

if [ -f "$AGENTS_FILE" ] && grep -q '^<!-- showme:start -->$' "$AGENTS_FILE"; then
  temporary="${AGENTS_FILE}.showme-tmp.$$"
  awk '
    /^<!-- showme:start -->$/ { removing=1; next }
    /^<!-- showme:end -->$/ { removing=0; next }
    !removing { print }
  ' "$AGENTS_FILE" > "$temporary"
  mv "$temporary" "$AGENTS_FILE"
fi

if [ "$REMOVE_CONFIG" = "yes" ]; then
  rm -rf "$CONFIG_DIR"
fi

rm -rf "$SHARE_DIR"
echo "ShowMe CLI and Codex integration removed. Remote pages were not changed."
if [ "$REMOVE_CONFIG" = "no" ]; then
  echo "Saved configuration retained at $CONFIG_DIR"
fi
