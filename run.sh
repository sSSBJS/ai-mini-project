#!/bin/bash
# Quick start script for semiconductor workflow
# Usage: ./run.sh [query]

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# Default query
QUERY="${1:-반도체 기술 동향과 전략을 분석해주세요.}"

echo "🚀 Starting Semiconductor Workflow..."
echo "Query: $QUERY"
echo ""

# Run with PYTHONPATH
PYTHONPATH="${PROJECT_DIR}/src" python3 "${PROJECT_DIR}/main.py" --query "$QUERY"
