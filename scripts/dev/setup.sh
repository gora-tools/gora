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

# 2. Install Python 3.12 via uv
echo "Installing Python 3.12..."
uv python install 3.12

# 3. Install all project dependencies (core + dev + api + slack)
echo "Installing dependencies..."
uv sync --all-extras

# 4. Verify
echo ""
echo "=== Verification ==="
uv run python --version
uv run gora version
echo ""
echo "=== Running tests ==="
uv run pytest --tb=short -v
echo ""
echo "=== Setup complete ==="
echo ""
echo "Available commands:"
echo "  uv run gora version                                        # CLI version"
echo "  uv run gora status -m contracts/examples/release-manifest.yaml  # Release status"
echo "  make test                                                   # Run tests"
echo "  make lint                                                   # Lint code"
echo "  make format                                                 # Format code"
