#!/bin/sh
set -eu

BIN_PATH=${HOME}/.local/bin/showme
SHARE_DIR=${HOME}/.local/share/showme
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

remove_preference() {
  instruction_file=$1
  if [ -n "$instruction_file" ] && [ -f "$instruction_file" ] && grep -q '^<!-- showme:start -->$' "$instruction_file"; then
    temporary=$(mktemp "${instruction_file}.showme.XXXXXX")
    awk '
      /^<!-- showme:start -->$/ { removing=1; next }
      /^<!-- showme:end -->$/ { removing=0; next }
      !removing { print }
    ' "$instruction_file" > "$temporary"
    mv "$temporary" "$instruction_file"
  fi
}

if [ -f "$MANIFEST" ]; then
  rm -f "$BIN_PATH"
  for line in 3 5; do
    skill_dir=$(sed -n "${line}p" "$MANIFEST")
    if [ -n "$skill_dir" ] && [ -f "$skill_dir/.showme-managed" ]; then
      rm -rf "$skill_dir"
    fi
  done
  for line in 4 6; do
    remove_preference "$(sed -n "${line}p" "$MANIFEST")"
  done
else
  echo "ShowMe installation manifest is missing; refusing to remove CLI, skills, or preferences automatically." >&2
fi

if [ "$REMOVE_CONFIG" = "yes" ]; then
  rm -rf "$CONFIG_DIR"
fi

rm -rf "$SHARE_DIR"
echo "ShowMe local removal complete. Remote pages were not changed."
if [ "$REMOVE_CONFIG" = "no" ]; then
  echo "Saved configuration retained at $CONFIG_DIR"
fi
