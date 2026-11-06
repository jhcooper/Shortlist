#!/bin/sh
# Installs the pinned gitleaks into ./.tools/ (gitignored) after checking its SHA-256. Used by the pre-commit hook,
# CI and the weekly review. Linux x64 only; elsewhere install gitleaks yourself (e.g. brew install gitleaks).
set -e
VER=8.21.2
SUM=5bc41815076e6ed6ef8fbecc9d9b75bcae31f39029ceb55da08086315316e3ba
cd "$(git rev-parse --show-toplevel)"
[ -x .tools/gitleaks ] && .tools/gitleaks version 2>/dev/null | grep -q "$VER" && exit 0
mkdir -p .tools
curl -sSLo .tools/gl.tgz "https://github.com/gitleaks/gitleaks/releases/download/v$VER/gitleaks_${VER}_linux_x64.tar.gz"
echo "$SUM  .tools/gl.tgz" | sha256sum -c - >/dev/null
tar -xzf .tools/gl.tgz -C .tools gitleaks && rm .tools/gl.tgz
echo "gitleaks $VER installed in .tools/"
