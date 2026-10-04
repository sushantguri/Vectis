#!/bin/bash
git init
git config user.name "Sushant"
git config user.email "sushantguri@example.com"
git remote add origin https://github.com/sushantguri/Vectis.git

echo ".venv/" > .gitignore
echo "__pycache__/" >> .gitignore
echo "*.egg-info/" >> .gitignore
echo ".DS_Store" >> .gitignore

git add .gitignore README.md pyproject.toml zshrc-snippet.sh
git commit -m "Initial project scaffolding and documentation"

git add vectis/__init__.py vectis/ui.py
git commit -m "Add UI utilities and core init"

git add vectis/state.py
git commit -m "Implement global state management and atomic updates"

git add vectis/netinfo.py
git commit -m "Add network interface and IP detection logic"

git add vectis/preflight.py
git commit -m "Implement dependency preflight checks (Homebrew)"

git add vectis/certs.py
git commit -m "Add SSL certificate generation and trust management"

git add vectis/sysdns.py
git commit -m "Add macOS local DNS resolver configuration"

git add vectis/supervisor.py
git commit -m "Implement process supervisor for backend services"

git add vectis/control.py vectis/fault.py
git commit -m "Add control plane listener and fault injection"

git add vectis/hosting.py vectis/live_logs.py vectis/team_logs.py
git commit -m "Add static hosting, live logs, and team log aggregation"

git add vectis/dashboard.py
git commit -m "Add team status dashboard aggregator"

git add vectis/roles/__init__.py vectis/roles/base.py 
git commit -m "Add base Role abstractions"

git add vectis/roles/dns_role.py vectis/roles/edge_role.py vectis/roles/backend_role.py
git commit -m "Implement DNS, Edge, and Backend specific roles"

git add vectis/templates/
git commit -m "Add configuration templates for dnsmasq and nginx"

git add vectis/cli.py bin/
git commit -m "Add main CLI entrypoints and commands"

git add .
git commit -m "Catch all remaining files and outputs"

