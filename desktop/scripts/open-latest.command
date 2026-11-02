#!/bin/zsh
set -eu
project_root="$(cd "$(dirname "$0")/../.." && pwd)"
client_binary="$project_root/desktop/out/Loops-darwin-arm64/Loops.app/Contents/MacOS/Loops"
if [[ ! -x "$client_binary" ]]; then
  print '尚未构建客户端，请从 desktop/ 执行 npm run build 和 npm run package:mac。'
  exit 1
fi
# Use normal Application Support data, with the existing project-local model configuration.
exec env -u LOOPS_DATA_DIR -u LOOPS_WORKSPACE_ROOT -u LOOPS_ELECTRON_DEV LOOPS_CONFIG_DIR="$project_root" "$client_binary"
