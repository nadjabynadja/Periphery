#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
PASS=0
FAIL=0

check() {
    local name="$1"
    local url="$2"
    local expect="$3"

    response=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    if [[ "$response" == "$expect" ]]; then
        echo "  [PASS] $name ($url) -> $response"
        ((PASS++))
    else
        echo "  [FAIL] $name ($url) -> $response (expected $expect)"
        ((FAIL++))
    fi
}

check_json() {
    local name="$1"
    local url="$2"
    local key="$3"

    body=$(curl -s "$url" 2>/dev/null || echo "{}")
    value=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('$key',''))" 2>/dev/null || echo "")
    if [[ -n "$value" ]]; then
        echo "  [PASS] $name -> $key=$value"
        ((PASS++))
    else
        echo "  [FAIL] $name -> $key not found in response"
        ((FAIL++))
    fi
}

echo "=== Periphery Smoke Test ==="
echo "Target: $BASE_URL"
echo ""

echo "Endpoint checks:"
check "Root"           "$BASE_URL/"              "200"
check "Health"         "$BASE_URL/health"        "200"
check "Pipeline stats" "$BASE_URL/pipeline/stats" "200"
check "API docs"       "$BASE_URL/docs"          "200"

echo ""
echo "Health details:"
check_json "Status"  "$BASE_URL/health" "status"
check_json "Vectors" "$BASE_URL/health" "vectors"

# Check frontend if built
response=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/app/" 2>/dev/null || echo "000")
if [[ "$response" == "200" ]]; then
    echo ""
    echo "  [PASS] Frontend served at /app/"
    ((PASS++))
else
    echo ""
    echo "  [SKIP] Frontend not built (run 'cd frontend && npm run build')"
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if (( FAIL > 0 )); then
    exit 1
fi
