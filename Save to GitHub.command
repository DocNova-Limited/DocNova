#!/bin/bash
# Uploads the latest DocNova website to GitHub (github.com/DocNova-Limited/DocNova).
# Private files (.env with passwords/keys, the private/ folder with customers and orders) are never uploaded.
cd "$(dirname "$0")" || exit 1
TOOLS="$HOME/.docnova-tools"; mkdir -p "$TOOLS"
GH="$(command -v gh || true)"
if [ -z "$GH" ] && [ -x "$TOOLS/gh/bin/gh" ]; then GH="$TOOLS/gh/bin/gh"; fi
if [ -z "$GH" ]; then
  echo "Getting GitHub's official sign-in tool (one time only)…"
  ARCH=$(uname -m); [ "$ARCH" = "arm64" ] || ARCH=amd64
  URL=$(curl -fsSL https://api.github.com/repos/cli/cli/releases/latest | grep -o "https://[^\"]*macOS_${ARCH}\.zip" | head -1)
  [ -n "$URL" ] || { echo "Could not find the download. Please send a screenshot to Claude."; read -r -p "Press Enter to close." _; exit 1; }
  curl -fsSL "$URL" -o "$TOOLS/gh.zip" && rm -rf "$TOOLS/gh" "$TOOLS/gh_"* && unzip -q "$TOOLS/gh.zip" -d "$TOOLS" && mv "$TOOLS"/gh_*_macOS_* "$TOOLS/gh" && rm "$TOOLS/gh.zip"
  GH="$TOOLS/gh/bin/gh"
fi
if ! "$GH" auth status -h github.com >/dev/null 2>&1; then
  echo
  echo "Sign in to GitHub: a code will appear below and your web browser will open."
  echo "Enter the code on the GitHub page, click Authorize, then come back to this window."
  echo
  "$GH" auth login -h github.com -p https -w || { read -r -p "Sign-in did not finish. Press Enter to close." _; exit 1; }
fi
"$GH" auth setup-git -h github.com >/dev/null 2>&1
echo
echo "Checking for changes on GitHub…"
git fetch -q origin || { echo "Could not reach GitHub."; read -r -p "Press Enter to close." _; exit 1; }
if [ -n "$(git log --oneline HEAD..origin/main)" ]; then
  echo "GitHub has changes that are not on this laptop yet. Nothing was uploaded — please tell Claude."
  read -r -p "Press Enter to close." _; exit 1
fi
N=$(git log --oneline origin/main..HEAD | wc -l | tr -d ' ')
if [ "$N" = "0" ]; then echo "GitHub is already up to date."; else
  git push origin HEAD:main && echo && echo "Done: $N updates saved to GitHub." || echo "Upload failed — please send a screenshot to Claude."
fi
read -r -p "You can close this window." _
