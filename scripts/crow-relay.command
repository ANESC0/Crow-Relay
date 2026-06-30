#!/bin/bash
# Lanceur macOS — double-cliquable depuis le Finder
cd "$(dirname "$0")"
exec bash crow-relay.sh "$@"
