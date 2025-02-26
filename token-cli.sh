#!/bin/bash

PORT="${PORT:-15432}"  # Default port is 15432, can be overridden with PORT env variable
BASE_URL="http://localhost:$PORT"

case "$1" in
    "next" | "n")
        # Cycle to next token
        curl -s -X POST "$BASE_URL/tokens/cycle" | jq -r '.current_index, .total_tokens' | xargs printf "Switched to token %d/%d\n"
        ;;
    "status" | "s")
        # Show token status
        curl -s "$BASE_URL/tokens/status" | jq -r '.tokens[] | "\(.index+1)/\(.total_tokens) \(if .is_current then "✓" else " " end) \(if .is_rate_limited then "⚠️" else "✅" end)"'
        ;;
    *)
        echo "Usage:"
        echo "  ./token-cli.sh next (or n)  - Switch to next token"
        echo "  ./token-cli.sh status (or s) - Show token status"
        ;;
esac