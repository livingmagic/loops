#!/bin/sh
set -eu
cd "$(dirname "$0")"
name='cpython-3.13.12+20260303-aarch64-apple-darwin-install_only_stripped.tar.gz'
target='.python/cpython-3.13.12+20260303-macos-aarch64'
if [ "$(uname -m)" != arm64 ]; then
  echo '当前许可与运行时清单仅验证了 Apple Silicon。' >&2
  exit 1
fi
mkdir -p .python/downloads
archive=".python/downloads/$name"
if [ ! -f "$archive" ]; then
  curl --fail --location --retry 3 --output "$archive" 'https://github.com/astral-sh/python-build-standalone/releases/download/20260303/cpython-3.13.12%2B20260303-aarch64-apple-darwin-install_only_stripped.tar.gz'
fi
echo "0103ab534b93472b1e2036fbb127a0dd4df73f437a4d8d7ce6fbe7e3d1f4fbbd  $archive" | shasum -a 256 --check
if [ -d "$target" ]; then
  echo '固定运行时已存在。'
  exit 0
fi
staging=$(mktemp -d .python/python-stage.XXXXXX)
trap 'rmdir "$staging" 2>/dev/null || true' EXIT
tar -xzf "$archive" -C "$staging"
mv "$staging/python" "$target"
echo "$target"
