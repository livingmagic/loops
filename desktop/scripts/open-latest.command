#!/bin/zsh
set -eu
project_root="$(cd "$(dirname "$0")/../.." && pwd)"
client_binary="$project_root/desktop/out/Loops-darwin-arm64/Loops.app/Contents/MacOS/Loops"
if [[ ! -x "$client_binary" ]]; then
  print '尚未构建客户端，请从 desktop/ 执行 npm run build 和 npm run package:mac。'
  exit 1
fi
# Packaged-client acceptance must never use the installed client's data or workspaces.
qa_data="$project_root/qa/desktop-client"
exec env -u LOOPS_ELECTRON_DEV LOOPS_DATA_DIR="$qa_data" LOOPS_WORKSPACE_ROOT="$qa_data/workspaces" LOOPS_CONFIG_DIR="$project_root" "$client_binary"
