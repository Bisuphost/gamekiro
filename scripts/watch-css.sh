#!/bin/bash
set -e
cd "$(dirname "$0")/.."
./bin/tailwindcss -i static_src/input.css -o static/css/output.css --watch
