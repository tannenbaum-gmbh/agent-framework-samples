#!/usr/bin/env bash
# Runs once after the devcontainer is created.
# Installs Python dependencies for the samples in this repository and
# upgrades the Azure CLI extensions/tools that are handy when working
# with Azure and Microsoft Foundry.
set -euo pipefail

echo "==> Upgrading pip"
python -m pip install --upgrade pip

echo "==> Installing sample dependencies"
if [ -f "samples/sequential-workflow-api/requirements.txt" ]; then
  pip install -r samples/sequential-workflow-api/requirements.txt
fi

echo "==> Azure CLI / Bicep versions"
az version || true
az bicep version || true

echo "Dev container ready. Run 'az login' to authenticate with Azure."
