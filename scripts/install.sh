#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BIN_DIR=${HOME}/.local/bin
SHARE_DIR=${HOME}/.local/share/showme
SKILL_DIR=${HOME}/.agents/skills/showme-publish
AGENTS_FILE=${CODEX_HOME:-${HOME}/.codex}/AGENTS.md
SERVER=""
TOKEN=""
CODEX_DEFAULT="ask"

usage() {
  echo "Usage: ./scripts/install.sh [--server URL] [--token TOKEN] [--codex-default|--no-codex-default]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --server) SERVER=${2:?Missing URL}; shift 2 ;;
    --token) TOKEN=${2:?Missing token}; shift 2 ;;
    --codex-default) CODEX_DEFAULT=yes; shift ;;
    --no-codex-default) CODEX_DEFAULT=no; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$BIN_DIR" "$SHARE_DIR" "$(dirname "$SKILL_DIR")"
if [ -e "$BIN_DIR/showme" ] && [ ! -f "$SHARE_DIR/install-manifest" ]; then
  echo "Refusing to overwrite an unmanaged command at $BIN_DIR/showme" >&2
  exit 1
fi
if [ -e "$SKILL_DIR" ] && [ ! -f "$SKILL_DIR/.showme-managed" ]; then
  echo "Refusing to overwrite an unmanaged skill at $SKILL_DIR" >&2
  exit 1
fi
cp "$REPO_DIR/cli/showme.py" "$BIN_DIR/showme"
chmod 755 "$BIN_DIR/showme"
cp "$REPO_DIR/scripts/uninstall.sh" "$SHARE_DIR/uninstall.sh"
chmod 755 "$SHARE_DIR/uninstall.sh"
rm -rf "$SKILL_DIR"
cp -R "$REPO_DIR/skill/showme-publish" "$SKILL_DIR"
touch "$SKILL_DIR/.showme-managed"
{
  echo "$BIN_DIR/showme"
  echo "$SHARE_DIR"
  echo "$SKILL_DIR"
  echo "$AGENTS_FILE"
} > "$SHARE_DIR/install-manifest"

case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *)
    echo ""
    echo "Add this directory to PATH if your shell does not already include it:"
    echo "  $BIN_DIR"
    ;;
esac

if [ "$CODEX_DEFAULT" = "ask" ]; then
  printf "Make ShowMe the default for Codex-generated webpages? [Y/n] "
  read -r answer
  case "$answer" in n|N|no|NO) CODEX_DEFAULT=no ;; *) CODEX_DEFAULT=yes ;; esac
fi

if [ "$CODEX_DEFAULT" = "yes" ]; then
  mkdir -p "$(dirname "$AGENTS_FILE")"
  touch "$AGENTS_FILE"
  AGENTS_TMP=$(mktemp "${AGENTS_FILE}.showme.XXXXXX")
  awk '
    /^<!-- showme:start -->$/ { managed = 1; next }
    /^<!-- showme:end -->$/ { managed = 0; next }
    !managed { print }
  ' "$AGENTS_FILE" > "$AGENTS_TMP"
  {
    echo ""
    echo '<!-- showme:start -->'
    echo '## Generated webpages'
    echo ''
    echo 'For any HTML report, dashboard, visual summary, or webpage intended for me to view, use the showme-publish skill, publish it automatically, and always return the clickable ShowMe URL without waiting for me to ask for the link. If I invoke or pin the skill, publishing and returning the link are required parts of completing the task unless I explicitly request local-only output.'
    echo '<!-- showme:end -->'
  } >> "$AGENTS_TMP"
  mv "$AGENTS_TMP" "$AGENTS_FILE"
fi

if [ -n "$SERVER" ] && [ -n "$TOKEN" ]; then
  "$BIN_DIR/showme" configure --server "$SERVER" --token "$TOKEN"
elif [ -n "$SERVER" ]; then
  "$BIN_DIR/showme" configure --server "$SERVER"
elif [ -n "$TOKEN" ]; then
  "$BIN_DIR/showme" configure --token "$TOKEN"
else
  "$BIN_DIR/showme" configure
fi

echo ""
echo "ShowMe installed. Restart Codex if the skill is not immediately visible."
echo "Try: showme doctor"
