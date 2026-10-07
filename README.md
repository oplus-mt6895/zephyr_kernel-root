# Zephyr Kernel Builder

Telegram-controlled kernel builder for **Realme GT Neo 3 (zephyr / MT6895)**.

## Features

- ⚡ GitHub Actions kernel builds
- 🌱 KernelSU-Next
- 🛡️ KernelSU
- 📦 AnyKernel3 ZIP
- 📊 Live Telegram build status
- 🧪 Actions artifact testing before release
- 🚀 Manual GitHub Release publishing
- 🗑️ Owner-only build deletion
- 🔄 Current Build detects an already-running build

## Kernel

- Linux 5.10
- Defconfig: `oplus6895_defconfig`
- Output: `Image.gz`
- Target: Realme GT Neo 3 / Zephyr / MT6895

## Telegram Bot

The bot provides:

```
/start      Open builder
/kernel     Open builder
/status     Check current build
/release    Publish an existing successful build
/id         Show Telegram user ID
```

Build flow:

```
🔨 Build Kernel
    ↓
🌱 Select Root (KernelSU-Next / KernelSU / SukiSU-Ultra / No Root)
    ↓
🚀 Start Build
    ↓
📊 Live Progress
    ↓
✅ Build Complete
    ↓
🚀 Publish Release
   OR
🧪 Actions Only
    ↓
📦 Publish later if wanted
```

The bot can also find a build that was started before the bot restarted.

## Setting Up Your Own Bot

### 1. Create a Telegram bot

Create a bot with **@BotFather** using `/newbot`.

Store the token as this GitHub Actions repository secret:

```
TELEGRAM_BOT_TOKEN
```

**Never commit the token to the repository.**

### 2. Set the Telegram commands in BotFather

After creating the bot, open **@BotFather** and configure its command menu:

**/mybots → Your Bot → Edit Bot → Edit Commands**

Paste:

```
start - Open Zephyr Kernel Builder
kernel - Open kernel builder
status - Check current build
release - Manage and publish builds
id - Show Telegram user ID
```

This only configures Telegram's command menu. The actual command handling is already implemented in `bot/bot.py`.

### 3. Configure the Telegram group

Add the bot to your group and give it permission to send/edit messages.

Set:

```
TELEGRAM_CHAT_ID
```

as a GitHub Actions repository secret containing the group chat ID.

### 4. Configure the owner

In `bot/bot.py`, set:

```python
DELETE_OWNER_ID = YOUR_TELEGRAM_USER_ID
```

Get your numeric Telegram ID with the bot's `/id` command.

Only this account can permanently delete an Actions build. Group administrators do not automatically receive delete permission.

### 5. Start the worker

Open:

**GitHub → Actions → Zephyr Telegram Bot → Run workflow**

The worker normally hands off to another worker automatically.

## Required Secrets

| Secret | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram BotFather token |
| `TELEGRAM_CHAT_ID` | Telegram group/chat ID |

## Release Workflow

A successful build is **not automatically published**.

The bot first offers:

- 🚀 **Publish GitHub Release**
- 🧪 **Actions Run / Download**

If you choose Actions Only, the build can be published later with:

```
/release
```

This keeps experimental/broken builds out of GitHub Releases.

## Security

- Never commit Telegram tokens.
- Keep repository Actions permissions restricted to what the workflows need.
- Delete-build permission is restricted to `DELETE_OWNER_ID`.
- Do not expose GitHub or Telegram credentials in logs.

## Maintainer

**almightygodthor**