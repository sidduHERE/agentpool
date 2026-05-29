#!/usr/bin/env bash
set -euo pipefail

repo="${AGENTPOOL_REPO:-sidduHERE/agentpool}"
version="${1:-${AGENTPOOL_VERSION:-latest}}"
installer="${AGENTPOOL_INSTALLER:-auto}"
python_bin="${AGENTPOOL_PYTHON:-python3.11}"
setup_clients="${AGENTPOOL_SETUP_CLIENTS:-}"
remove_legacy="${AGENTPOOL_REMOVE_LEGACY:-1}"

if [[ "$version" != "latest" && "$version" != v* ]]; then
  version="v${version}"
fi

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "agentpool installer: missing required command: $1" >&2
    exit 1
  fi
}

need gh

if [[ "$installer" == "auto" ]]; then
  if command -v uv >/dev/null 2>&1; then
    installer="uv"
  elif command -v pipx >/dev/null 2>&1; then
    installer="pipx"
  else
    echo "agentpool installer: install uv or pipx first" >&2
    echo "  uv:   https://docs.astral.sh/uv/getting-started/installation/" >&2
    echo "  pipx: https://pipx.pypa.io/stable/installation/" >&2
    exit 1
  fi
fi

case "$installer" in
  uv) need uv ;;
  pipx) need pipx ;;
  *)
    echo "agentpool installer: AGENTPOOL_INSTALLER must be auto, uv, or pipx" >&2
    exit 1
    ;;
esac

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/agentpool-install.XXXXXX")"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

if [[ "$version" == "latest" ]]; then
  tag="$(gh release view --repo "$repo" --json tagName --jq '.tagName')"
else
  tag="$version"
fi

echo "agentpool installer: downloading $repo@$tag"
gh release download "$tag" \
  --repo "$repo" \
  --pattern '*.whl' \
  --dir "$tmp_dir" \
  --clobber >/dev/null

wheels=()
while IFS= read -r candidate; do
  wheels+=("$candidate")
done < <(find "$tmp_dir" -maxdepth 1 -type f -name '*.whl' | sort)
if [[ "${#wheels[@]}" -ne 1 ]]; then
  echo "agentpool installer: expected one wheel asset, found ${#wheels[@]}" >&2
  printf '  %s\n' "${wheels[@]:-}" >&2
  exit 1
fi
wheel="${wheels[0]}"
wheel_name="$(basename "$wheel")"

digest="$(gh release view "$tag" --repo "$repo" --json assets --jq ".assets[] | select(.name == \"$wheel_name\") | .digest" || true)"
if [[ "$digest" == sha256:* ]] && command -v shasum >/dev/null 2>&1; then
  expected="${digest#sha256:}"
  actual="$(shasum -a 256 "$wheel" | awk '{print $1}')"
  if [[ "$expected" != "$actual" ]]; then
    echo "agentpool installer: SHA256 mismatch for $wheel_name" >&2
    echo "  expected: $expected" >&2
    echo "  actual:   $actual" >&2
    exit 1
  fi
  echo "agentpool installer: verified sha256:$actual"
else
  echo "agentpool installer: SHA256 verification skipped; no digest in release metadata"
fi

if [[ "$installer" == "uv" ]]; then
  if [[ "$remove_legacy" == "1" ]] && uv tool list | grep -q '^agentpool '; then
    echo "agentpool installer: removing legacy uv tool env named agentpool"
    uv tool uninstall agentpool >/dev/null || true
  fi
  echo "agentpool installer: installing with uv tool"
  uv tool install --force --python "$python_bin" "$wheel"
else
  if [[ "$remove_legacy" == "1" ]] && pipx list --short 2>/dev/null | grep -q '^agentpool '; then
    echo "agentpool installer: removing legacy pipx venv named agentpool"
    pipx uninstall agentpool >/dev/null || true
  fi
  echo "agentpool installer: installing with pipx"
  pipx install --force --python "$python_bin" "$wheel"
fi

echo "agentpool installer: installed $(agentpool --version)"
agentpool config validate --json >/dev/null
agentpool models validate --json >/dev/null

if [[ -n "$setup_clients" ]]; then
  IFS=',' read -r -a clients <<<"$setup_clients"
  for client in "${clients[@]}"; do
    client="${client//[[:space:]]/}"
    [[ -z "$client" ]] && continue
    echo "agentpool installer: configuring MCP client $client"
    agentpool mcp-config --client "$client" --absolute-command --install
  done
else
  cat <<'EOF'

AgentPool is installed. Next useful checks:

  agentpool doctor --deep --privacy
  agentpool smoke --provider fake-question --repo . --json

To configure MCP hosts, run the clients you actually use:

  agentpool mcp-config --client codex --absolute-command --install
  agentpool mcp-config --client claude-code --absolute-command --install
  agentpool mcp-config --client cursor --absolute-command --install
  agentpool mcp-config --client copilot-cli --absolute-command --install

Or rerun this installer with:

  AGENTPOOL_SETUP_CLIENTS=codex,claude-code scripts/install.sh
EOF
fi
