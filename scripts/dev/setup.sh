#!/usr/bin/env bash
set -euo pipefail

# GORA local development setup for macOS
# Run: bash scripts/dev/setup.sh

echo "=== GORA Dev Environment Setup ==="

# 1. Install uv (Python package manager)
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo "Restart your shell or run: source \$HOME/.local/bin/env"
else
    echo "uv already installed: $(uv --version)"
fi

# 2. Install a Python of the minor version .python-version names, via uv (pyproject.toml
#    sets the lowest uv this project runs with: upgrade uv if it refuses to run)
echo "Installing Python $(cat .python-version)..."
uv python install

# 3. Install all project dependencies (core + api + slack, and the test and check groups)
echo "Installing dependencies..."
make sync

# 4. Verify
echo ""
echo "=== Verification ==="
uv run --no-sync python --version
uv run --no-sync gora version
echo ""
echo "=== Running tests ==="
make test
echo ""
echo "=== Setup complete ==="
echo ""
echo "Available commands:"
echo "  uv run gora version                                        # CLI version"
echo "  uv run gora status -m contracts/examples/release-manifest.yaml  # Release status"
echo "  make test                                                   # Run tests"
echo "  make lint                                                   # Lint code"
echo "  make format                                                 # Format code"
