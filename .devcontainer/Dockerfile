FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

USER root

# Refresh the inherited Yarn repository before features run apt-get update.
RUN curl -fsSL https://dl.yarnpkg.com/debian/pubkey.gpg -o /tmp/yarn-pubkey.gpg \
    && gpg --batch --yes --dearmor -o /usr/share/keyrings/yarn-archive-keyring.gpg /tmp/yarn-pubkey.gpg \
    && chmod 0644 /usr/share/keyrings/yarn-archive-keyring.gpg \
    && printf '%s\n' 'deb [signed-by=/usr/share/keyrings/yarn-archive-keyring.gpg] https://dl.yarnpkg.com/debian/ stable main' > /etc/apt/sources.list.d/yarn.list \
    && rm /tmp/yarn-pubkey.gpg

USER vscode
