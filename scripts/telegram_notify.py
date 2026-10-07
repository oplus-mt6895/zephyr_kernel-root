#!/usr/bin/env python3
import html
import json
import os
import sys
import urllib.parse
import urllib.request


def api(method: str, fields: dict) -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {}
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())


def send(text: str, keyboard=None) -> None:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        print("Telegram chat ID not configured; skipping notification.")
        return
    fields = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if keyboard:
        fields["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    result = api("sendMessage", fields)
    if result and not result.get("ok", False):
        raise RuntimeError(result.get("description", "Telegram API error"))


def main() -> int:
    state = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    run_id = os.environ.get("RUN_ID", "-")
    run_number = os.environ.get("RUN_NUMBER", "-")
    owner_repo = os.environ.get("GITHUB_REPOSITORY", "almightygodthor/zephyr-kernel-builder")
    actions_url = f"https://github.com/{owner_repo}/actions/runs/{run_id}"
    root = os.environ.get("ROOT_IMPL", "ksu-next")
    root_label = {"ksu-next": "KernelSU-Next", "kernel-su": "KernelSU", "sukisu-ultra": "SukiSU-Ultra", "baka-su": "BakaSU"}.get(root, "No Root")
    root_releases = {
        "ksu-next": "https://t.me/ksunext_ci",
        "kernel-su": "https://github.com/tiann/KernelSU/releases/tag/v3.0.0",
        "sukisu-ultra": "https://t.me/sukisuultra",
        "baka-su": "https://github.com/Baka-SU/BakaSU/actions/workflows/build-manager.yml",
    }.get(root)
    icon = {"started": "🚀", "success": "✅", "failure": "❌", "cancelled": "🛑"}.get(state, "ℹ️")

    if state == "success":
        body = (
            "<b>⚡ ZEPHYR · BUILD COMPLETE</b>\n\n"
            "📱 GT Neo 3 · zephyr\n"
            "🧩 Linux 5.10 · MT6895\n"
            f"🌱 {html.escape(root_label)} · 📦 AK3\n\n"
            "🧪 <b>Actions artifact is ready for testing.</b>\n"
            f"🆔 Run <code>#{html.escape(run_number)}</code>"
        )
        keyboard = [
            [{"text": "🚀 Publish GitHub Release", "callback_data": f"publish:{run_id}:{run_number}"}],
            [{"text": "🧪 Actions Run / Download", "url": actions_url}],
            [{"text": "🗑️ Delete Build", "callback_data": f"deletebuild:{run_id}"}],
        ]
    elif state == "published":
        release_url = os.environ.get("RELEASE_URL", "")
        download_url = os.environ.get("DOWNLOAD_URL", "")
        body = (
            "<b>🚀 ZEPHYR · RELEASE PUBLISHED</b>\n\n"
            "📱 GT Neo 3 · zephyr\n"
            "🧩 Linux 5.10 · MT6895\n"
            f"🌱 {html.escape(root_label)} · 📦 AK3\n"
            f"🆔 Run <code>#{html.escape(run_number)}</code>"
        )
        keyboard = []
        if download_url:
            keyboard.append([{"text": "⬇️ Download Kernel ZIP", "url": download_url}])
        if release_url:
            keyboard.append([{"text": "📦 GitHub Release ↗", "url": release_url}])
        if root_releases:
            keyboard.append([{"text": f"🌱 {root_label} ↗", "url": root_releases}])
    elif state == "started":
        # The Telegram worker already owns the single live build card.
        # Avoid creating a second BUILD STARTED message from the Actions workflow.
        return 0
    else:
        body = (
            f"<b>{icon} ZEPHYR · BUILD {html.escape(state.upper())}</b>\n\n"
            "📱 GT Neo 3 · zephyr\n"
            f"🌱 {html.escape(root_label)} · 📦 AK3\n"
            f"🆔 Run <code>#{html.escape(run_number)}</code>"
        )
        keyboard = [
            [{"text": "🔗 GitHub Actions ↗", "url": actions_url}],
            [{"text": "🗑️ Delete Build", "callback_data": f"deletebuild:{run_id}"}],
        ]
    send(body, keyboard)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())