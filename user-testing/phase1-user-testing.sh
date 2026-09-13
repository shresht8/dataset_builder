#!/usr/bin/env bash
#
# phase1-user-testing.sh — manual walkthrough of everything built in Phase 1
# (steel thread, data model, local users + dev login + RBAC, PATs).
#
# Usage:
#   Run it top to bottom:         bash user-testing/phase1-user-testing.sh
#   Or copy/paste sections into a terminal to explore interactively.
#
# Assumes: Ubuntu/WSL (real curl), run from the repo root, with the Docker
# stack up (see section 0). Expected status codes are noted inline.
#
# Cookie jars created here (one jar = one logged-in identity):
#   admin.txt · editor.txt · annotator.txt
set -u

BASE=localhost:8000
CT='Content-Type: application/json'

# Extract the first top-level JSON string field from stdin.
# Pure grep/sed — no jq or python dependency, works anywhere curl does.
json_field() { grep -oE "\"$1\":\"[^\"]*\"" | head -1 | sed -E 's/.*:"([^"]*)"/\1/'; }

echo "================================================================"
echo " 0. Bring up the Docker stack (Postgres -> MinIO -> API)"
echo "================================================================"
# If 'docker' errors with 'daemon not running', start Docker Desktop first.
docker compose -f infra/docker-compose.yml up -d
sleep 8
docker ps --format '{{.Names}}: {{.Status}}'

echo
echo "================================================================"
echo " 1. Health check (proves HTTP -> API -> Postgres)"
echo "================================================================"
# Expect: {"status":"ok"}
curl -s $BASE/v1/health; echo

echo
echo "================================================================"
echo " 2. Log in as the seeded admin (admin@example.com)"
echo "================================================================"
# -c captures the session cookie into admin.txt; -b sends it back.
curl -s -c admin.txt -X POST $BASE/v1/auth/login -H "$CT" -d '{"email":"admin@example.com"}'; echo
echo "-- who am I (expect role: admin) --"
curl -s -b admin.txt $BASE/v1/auth/me; echo

echo
echo "================================================================"
echo " 3. Create editor + annotator users (admin-only)"
echo "================================================================"
curl -s -b admin.txt -X POST $BASE/v1/users -H "$CT" -d '{"email":"editor@example.com","role":"editor"}'; echo
curl -s -b admin.txt -X POST $BASE/v1/users -H "$CT" -d '{"email":"annotator@example.com","role":"annotator"}'; echo
echo "-- list users --"
curl -s -b admin.txt $BASE/v1/users; echo

echo
echo "================================================================"
echo " 4. Give editor and annotator their own cookie jars"
echo "================================================================"
curl -s -c editor.txt    -X POST $BASE/v1/auth/login -H "$CT" -d '{"email":"editor@example.com"}'    >/dev/null
curl -s -c annotator.txt -X POST $BASE/v1/auth/login -H "$CT" -d '{"email":"annotator@example.com"}' >/dev/null
echo "editor.txt and annotator.txt written"

echo
echo "================================================================"
echo " 5. Auth negative tests"
echo "================================================================"
echo "-- unknown user (expect 401) --"
curl -s -X POST $BASE/v1/auth/login -H "$CT" -d '{"email":"nobody@example.com"}' -w '\n%{http_code}\n'
echo "-- anonymous, no cookie (expect 401) --"
curl -s $BASE/v1/datasets -w '\n%{http_code}\n'
echo "-- annotator hitting an admin endpoint (expect 403) --"
curl -s -b annotator.txt $BASE/v1/users -w '\n%{http_code}\n'

echo
echo "================================================================"
echo " 6. Data model — editor creates a dataset"
echo "================================================================"
DID=$(curl -s -b editor.txt -X POST $BASE/v1/datasets -H "$CT" \
      -d '{"name":"demo","description":"test dataset"}' | tee /dev/stderr | json_field id)
echo
echo "dataset id = $DID"

echo
echo "================================================================"
echo " 7. Schema — editor allowed, annotator blocked"
echo "================================================================"
echo "-- editor defines schema (expect 200) --"
curl -s -b editor.txt -X PUT $BASE/v1/datasets/$DID/schema -H "$CT" \
  -d '{"columns":[{"key":"input","label":"Input","type":"long_text"},{"key":"verdict","label":"Verdict","type":"select","options":["correct","incorrect"]}]}' \
  -w '\n%{http_code}\n'
echo "-- annotator tries to change schema (expect 403) --"
curl -s -b annotator.txt -X PUT $BASE/v1/datasets/$DID/schema -H "$CT" \
  -d '{"columns":[]}' -w '\n%{http_code}\n'

echo
echo "================================================================"
echo " 8. Schema validation (each expect 422)"
echo "================================================================"
echo "-- select type with no options --"
curl -s -b editor.txt -X PUT $BASE/v1/datasets/$DID/schema -H "$CT" \
  -d '{"columns":[{"key":"x","label":"X","type":"select"}]}' -w '\n%{http_code}\n'
echo "-- invalid column type --"
curl -s -b editor.txt -X PUT $BASE/v1/datasets/$DID/schema -H "$CT" \
  -d '{"columns":[{"key":"x","label":"X","type":"banana"}]}' -w '\n%{http_code}\n'

echo
echo "================================================================"
echo " 9. Rows — read schema back, annotator adds a row, list rows"
echo "================================================================"
curl -s -b editor.txt $BASE/v1/datasets/$DID/schema; echo
echo "-- annotator adds row data (allowed) --"
curl -s -b annotator.txt -X POST $BASE/v1/datasets/$DID/rows -H "$CT" \
  -d '{"data":{"input":"What is 2+2?","verdict":"correct"}}'; echo
echo "-- list rows --"
curl -s -b editor.txt $BASE/v1/datasets/$DID/rows; echo

echo
echo "================================================================"
echo "10. PATs — machine access (issue, use, revoke)"
echo "================================================================"
PAT_JSON=$(curl -s -b editor.txt -X POST $BASE/v1/auth/tokens -H "$CT" -d '{"name":"my-cli"}')
echo "$PAT_JSON"
RAW=$(echo "$PAT_JSON" | json_field token)
TID=$(echo "$PAT_JSON" | json_field id)
echo
echo "-- call the API with ONLY the bearer token (expect the editor user) --"
curl -s -H "Authorization: Bearer $RAW" $BASE/v1/auth/me; echo
echo "-- list tokens (no secret returned) --"
curl -s -b editor.txt $BASE/v1/auth/tokens; echo
echo "-- revoke the token (expect 204) --"
curl -s -b editor.txt -X DELETE $BASE/v1/auth/tokens/$TID -w '\n%{http_code}\n'
echo "-- reuse the revoked token (expect 401) --"
curl -s -H "Authorization: Bearer $RAW" $BASE/v1/auth/me -w '\n%{http_code}\n'

echo
echo "================================================================"
echo "11. Confirm persistence directly in Postgres (bypass the API)"
echo "================================================================"
docker exec infra-postgres-1 psql -U groundline -d groundline -c "SELECT id, data, status FROM dataset_rows;"
echo "-- all 8 tables --"
docker exec infra-postgres-1 psql -U groundline -d groundline -c "\dt"
echo "-- the three enums --"
docker exec infra-postgres-1 psql -U groundline -d groundline -c "\dT+"

echo
echo "================================================================"
echo " Done. Stop just the API (keep Postgres + MinIO up for Phase 2):"
echo "   docker compose -f infra/docker-compose.yml stop api"
echo " Cookie jars left behind: admin.txt editor.txt annotator.txt"
echo "================================================================"
