FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# System dependencies for Calibre-Web and common optional features
RUN rm -f /etc/apt/sources.list.d/yarn.list /etc/apt/sources.list.d/yarn.sources \
    && apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
        libmagic1 \
        imagemagick \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies for the app and test tooling
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir pytest pytest-cov diff-cover pytest-playwright playwright==1.65.2 requests \
    && rm /tmp/requirements.txt

# Install Playwright browser and OS dependencies (Chromium) for E2E tests
RUN mkdir -p /ms-playwright \
    && playwright install --with-deps chromium \
    && chmod -R 755 /ms-playwright

WORKDIR /workspaces/calibre-web
