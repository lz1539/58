$ErrorActionPreference = 'Stop'

uv run --with pyinstaller pyinstaller `
  --clean `
  --noconfirm `
  --onefile `
  --name edge_58_launcher `
  main.py
