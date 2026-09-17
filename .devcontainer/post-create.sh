#!/usr/bin/env bash
# Runs once after the devcontainer is created.
# Installs Python dependencies for the samples in this repository and
# upgrades the Azure CLI extensions/tools that are handy when working
# with Azure and Microsoft Foundry.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="${repo_root}/.venv"
requirements_files=(
  "${repo_root}/samples/sequential-workflow-api/requirements-dev.txt"
  "${repo_root}/samples/sandbox-code-agent-evals/requirements-dev.txt"
  "${repo_root}/samples/sandbox-hosted-agent/requirements-dev.txt"
)

echo "==> Creating Python virtual environment"
python -m venv --clear "${venv_dir}"

echo "==> Upgrading pip"
"${venv_dir}/bin/python" -m pip install --upgrade pip

echo "==> Installing sample dependencies"
for requirements_file in "${requirements_files[@]}"; do
  if [ -f "${requirements_file}" ]; then
    "${venv_dir}/bin/python" -m pip install -r "${requirements_file}"
  fi
done

if [ -f /etc/apt/sources.list.d/yarn.list ]; then
  echo "==> Refreshing the Yarn repository signing key"
  curl -fsSL https://dl.yarnpkg.com/debian/pubkey.gpg \
    | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/yarn-archive-keyring.gpg
fi

echo "==> Installing the latest Azure CLI"
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

echo "==> Installing the latest Bicep CLI"
az bicep install

echo "==> Azure CLI / Bicep versions"
az version
az bicep version

echo "Dev container ready. Run 'az login' to authenticate with Azure."
