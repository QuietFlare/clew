# Sourced by every shell in the Codespace. Own keys win; without them the
# agent and triage go through QuietFlare's proxy, which identifies the
# caller by the Codespace's GitHub token and stops at a small allowance.
# Nothing here runs outside a Codespace.
if [ -n "${CODESPACES:-}" ] && [ -n "${GITHUB_TOKEN:-}" ]; then
  if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    export ANTHROPIC_BASE_URL="https://proxy.quietflare.net"
    export ANTHROPIC_AUTH_TOKEN="$GITHUB_TOKEN"
  fi
  if [ -z "${TYPESAFE_API_KEY:-}" ]; then
    export CLEW_JEV_ENDPOINT="https://proxy.quietflare.net/typesafe/v1/systemone"
    export TYPESAFE_API_KEY="$GITHUB_TOKEN"
  fi
fi
