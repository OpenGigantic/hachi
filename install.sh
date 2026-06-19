#!/usr/bin/env bash
#
# Hachi one-liner installer (macOS / Linux)
#
#   curl -fsSL https://raw.githubusercontent.com/OpenGigantic/hachi/main/install.sh | bash
#
# Bootstraps uv (which manages Python), fetches Hachi, installs deps, and
# launches the guided setup. Override the source repo or install location with
# the HACHI_REPO / HACHI_HOME environment variables.
set -euo pipefail

REPO_URL="${HACHI_REPO:-https://github.com/OpenGigantic/hachi}"
INSTALL_DIR="${HACHI_HOME:-$HOME/.hachi-src}"
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
  info "Updating Hachi in $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  info "Cloning Hachi into $INSTALL_DIR..."
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

# 3. Install dependencies.
cd "$INSTALL_DIR"
info "Installing dependencies (this can take a minute)..."
uv sync

# 4. Drop a friendly launcher on PATH.
mkdir -p "$BIN_DIR"
cat >"$BIN_DIR/hachi" <<EOF
#!/usr/bin/env bash
exec uv run --project "$INSTALL_DIR" hachi "\$@"
EOF
chmod +x "$BIN_DIR/hachi"

# 5. Launch the guided setup.
info "Installed! Launching setup..."
"$BIN_DIR/hachi" quickstart || true

cat <<EOF

────────────────────────────────────────────────────────
Hachi is installed. If the 'hachi' command isn't found,
add this to your shell profile (~/.bashrc or ~/.zshrc):

  export PATH="\$HOME/.local/bin:\$PATH"

Then start chatting:

  hachi run
────────────────────────────────────────────────────────
EOF
