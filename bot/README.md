# ThunderGod's Zephyr Build Bot

GitHub-native Telegram control bot for `oplus-mt6895/zephyr_kernel-root`.

## How it works

The bot does **not** need Render, Railway, a VPS, a PC, or any other always-on server.

```
Telegram
   ↓
GitHub Actions (every 5 minutes)
   ↓
bot/bot.py
   ↓
GitHub Actions build.yml
   ↓
Kernel build + GitHub Release
   ↓
Telegram notification
```

The Telegram worker is deliberately short-lived. Each scheduled Actions run checks Telegram for pending commands/buttons, performs the requested operation, confirms the processed updates, and exits.

GitHub's scheduler starts it again automatically. The scheduled workflow runs from the repository's default branch, so the automatic 5-minute service becomes active once this workflow is on `main`.

## Commands

```
/start  - Open Zephyr Kernel Builder
/kernel  - Build a Zephyr kernel
/status  - Show the active build
/cancel  - Cancel the active build
```

## Bot flow

1. Send `/kernel` in the build group.
2. Choose **KernelSU-Next**, **KernelSU**, **SukiSU-Ultra**, **BakaSU**, or **No Root**.
4. Confirm **START BUILD**.
5. The bot dispatches `.github/workflows/build.yml`.
6. The existing build workflow sends start/completion/failure notifications and publishes the release.
7. Use `/status` at any time to see the active run.

Only Telegram group administrators can control builds.

## Required secret

Only this secret is required by the GitHub-native bot worker:

```
TELEGRAM_BOT_TOKEN
```

The existing `TELEGRAM_CHAT_ID` secret is still used by the kernel build workflow for build notifications.

No GitHub PAT is required for the worker. It uses the workflow's built-in `GITHUB_TOKEN` with:

- Actions: write
- Contents: read

GitHub permits a workflow authenticated with `GITHUB_TOKEN` to create new workflow runs through `workflow_dispatch`.

## Testing

While this bot is on a non-default branch, use the **Run workflow** button for **Zephyr Telegram Bot** and select `zephyr-bot-v2`.

After the final version is placed on `main`, the `schedule` trigger takes over automatically. You do not need to manually start the Telegram worker before using the bot each time.

## Local testing

Local execution is optional and requires a GitHub token with permission to dispatch/cancel Actions workflows:

```bash
export TELEGRAM_BOT_TOKEN='...'
export GITHUB_TOKEN='...'
export GITHUB_REPOSITORY='oplus-mt6895/zephyr_kernel-root'
export GITHUB_WORKFLOW='build.yml'
export GITHUB_REF='main'
python3 bot/bot.py
```

Do not commit real credentials.

## BotFather

Recommended commands:

```
start - Open Zephyr Kernel Builder
kernel - Build a Zephyr kernel
status - Show the active build
```

Keep the existing bot account/token. Add the bot to the kernel build group and promote it to an administrator.