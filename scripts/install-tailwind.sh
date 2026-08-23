#!/bin/bash
set -e
cd "$(dirname "$0")/.."

os="$(uname -s)"
arch="$(uname -m)"

case "$os" in
  Darwin)
    case "$arch" in
      arm64) asset="tailwindcss-macos-arm64" ;;
      *) asset="tailwindcss-macos-x64" ;;
    esac
    ;;
  Linux)
    case "$arch" in
      aarch64) asset="tailwindcss-linux-arm64" ;;
      *) asset="tailwindcss-linux-x64" ;;
    esac
    ;;
  *)
    echo "Unsupported OS: $os"
    exit 1
    ;;
esac

mkdir -p bin
curl -sSL -o bin/tailwindcss "https://github.com/tailwindlabs/tailwindcss/releases/latest/download/$asset"
chmod +x bin/tailwindcss
