#!/usr/bin/env bash
set -euo pipefail

: "${FOUNDRY_PROJECT_ENDPOINT:?Set FOUNDRY_PROJECT_ENDPOINT first.}"

agent_name="${AGENT_NAME:-mslearn-prompt-agent}"
question="${1:-What is Microsoft Agent Framework?}"
endpoint="${FOUNDRY_PROJECT_ENDPOINT%/}/agents/${agent_name}/endpoint/protocols/a2a"
message_id="a2a-test-$(date +%s)-$$"
timeout_seconds="${A2A_TIMEOUT_SECONDS:-120}"
token="$(az account get-access-token \
  --resource https://ai.azure.com \
  --query accessToken \
  --output tsv)"

send_payload="$(jq -n \
  --arg id "$message_id" \
  --arg text "$question" \
  '{
    jsonrpc: "2.0",
    id: $id,
    method: "SendMessage",
    params: {
      message: {
        messageId: $id,
        role: "ROLE_USER",
        parts: [{text: $text}]
      }
    }
  }')"

call_a2a() {
  curl --silent --show-error --verbose \
    --request POST "$endpoint" \
    --header "Authorization: Bearer $token" \
    --header "Content-Type: application/json" \
    --header "A2A-Version: 1.0" \
    --data "$1"
}

response="$(call_a2a "$send_payload")"
task_id="$(jq -r '.result.task.id // empty' <<<"$response")"
state="$(jq -r '.result.task.status.state // empty' <<<"$response")"

if [[ -z "$task_id" || -z "$state" ]]; then
  jq . <<<"$response"
  exit 0
fi

deadline=$((SECONDS + timeout_seconds))
while [[ "$state" == "TASK_STATE_SUBMITTED" || \
  "$state" == "TASK_STATE_WORKING" ]]; do
  if ((SECONDS >= deadline)); then
    printf 'Timed out after %s seconds waiting for task %s\n' \
      "$timeout_seconds" "$task_id" >&2
    exit 1
  fi

  sleep 2
  get_payload="$(jq -n \
    --arg request_id "${message_id}-get" \
    --arg task_id "$task_id" \
    '{
      jsonrpc: "2.0",
      id: $request_id,
      method: "GetTask",
      params: {id: $task_id}
    }')"
  response="$(call_a2a "$get_payload")"
  state="$(jq -r '.result.status.state // empty' <<<"$response")"
  printf 'Task state: %s\n' "${state:-unknown}" >&2
done

jq . <<<"$response"

if [[ "$state" != "TASK_STATE_COMPLETED" ]]; then
  exit 1
fi
