#!/usr/bin/env bash
#
# Tokoboros one-liner installer (macOS / Linux)
#
#   curl -fsSL https://raw.githubusercontent.com/opensource/tokoboros/main/install.sh | bash
#
# Bootstraps uv (which manages Python), fetches Tokoboros, installs deps, and
# launches the guided setup. Override the source repo or install location with
# the TOKOBOROS_REPO / TOKOBOROS_HOME environment variables.
set -euo pipefail

REPO_URL="${TOKOBOROS_REPO:-https://github.com/opensource/tokoboros}"
INSTALL_DIR="${TOKOBOROS_HOME:-$HOME/.tokoboros-src}"
BIN_DIR="$HOME/.local/bin"

info() { printf "\033[36m==>\033[0m %s\n" "$1"; }
err() { printf "\033[31mError:\033[0m %s\n" "$1" >&2; }

# 1. Ensure uv (it provisions a suitable Python automatically).
if ! command -v uv >/dev/null 2>&1; then
  info "Installing uv (Python toolchain manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v git >/dev/null 2>&1; then
  err "git is required but not installed. Please install git and re-run."
  exit 1
fi

# 2. Fetch (or update) the source.
if [ -d "$INSTALL_DIR/.git" ]; then
  info "Updating Tokoboros in $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  info "Cloning Tokoboros into $INSTALL_DIR..."
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

# 3. Install dependencies.
cd "$INSTALL_DIR"
info "Installing dependencies (this can take a minute)..."
uv sync

# 4. Drop a friendly launcher on PATH.
mkdir -p "$BIN_DIR"
cat >"$BIN_DIR/tokoboros" <<EOF
#!/usr/bin/env bash
exec uv run --project "$INSTALL_DIR" tokoboros "\$@"
EOF
chmod +x "$BIN_DIR/tokoboros"

# 5. Launch the guided setup.
info "Installed! Launching setup..."
"$BIN_DIR/tokoboros" quickstart || true

cat <<EOF

────────────────────────────────────────────────────────
Tokoboros is installed. If the 'tokoboros' command isn't found,
add this to your shell profile (~/.bashrc or ~/.zshrc):

  export PATH="\$HOME/.local/bin:\$PATH"

Then start chatting:

  tokoboros run
────────────────────────────────────────────────────────
EOF
