#!/usr/bin/env python3
"""Long-lived GitHub Actions Telegram worker for the Zephyr Kernel Builder."""

import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "oplus-mt6895/zephyr_kernel-root")
BUILD_WORKFLOW = "kernel-build.yml"
BOT_WORKFLOW = "telegram-bot.yml"
RELEASE_WORKFLOW = "publish-release.yml"
BRANCH = os.environ.get("GITHUB_REF_NAME") or os.environ.get("GITHUB_REF", "main").removeprefix("refs/heads/")
DELETE_OWNER_ID = 7577854738

# Keep comfortably below GitHub's 6-hour GitHub-hosted job limit.
WORKER_SECONDS = 60 * 60
POLL_TIMEOUT = 2
MONITOR_INTERVAL = 0.5
GH_PROGRESS_INTERVAL = 1
TRACKED_RUNS = {}
STOP_REQUESTED = False
LAST_BOT_MESSAGES = {}
SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"
GH_API = "https://api.github.com"


class APIError(RuntimeError):
    pass


def http_json(url, method="GET", data=None, headers=None, timeout=30):
    body = None
    hdrs = {"User-Agent": "Zephyr-GitHub-Telegram-Worker"}
    if headers:
        hdrs.update(headers)
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise APIError(f"HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise APIError(f"network error: {exc.reason}") from exc


def tg(method, data=None, timeout=20):
    result = http_json(f"{TG_API}/{method}", method="POST", data=data, timeout=timeout)
    if not result.get("ok"):
        raise APIError(result.get("description", f"Telegram {method} failed"))
    return result.get("result")


def gh(method, path, data=None):
    if not GH_TOKEN:
        raise APIError("GITHUB_TOKEN is not available")
    return http_json(
        f"{GH_API}{path}",
        method=method,
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GH_TOKEN}",
            "X-GitHub-Api-Version": "2026-03-10",
        },
        timeout=30,
    )


def esc(value):
    return html.escape(str(value), quote=False)


def send(chat_id, text, keyboard=None, message_id=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}

    if message_id is not None:
        payload["message_id"] = message_id
        try:
            result = tg("editMessageText", payload)
            LAST_BOT_MESSAGES[chat_id] = message_id
            return result
        except APIError as exc:
            if "message is not modified" not in str(exc).lower():
                print(f"message edit failed: {exc}", file=sys.stderr)
            return None

    return tg("sendMessage", payload)


def send_fresh(chat_id, text, keyboard=None):
    result = send(chat_id, text, keyboard)
    if isinstance(result, dict) and result.get("message_id"):
        LAST_BOT_MESSAGES[chat_id] = result["message_id"]
    return result


def edit_message(chat_id, message_id, text, keyboard=None):
    """Edit an existing Telegram message; raise if it cannot be edited."""
    if message_id is None:
        return send_fresh(chat_id, text, keyboard)
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard is not None:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        result = tg("editMessageText", payload)
        LAST_BOT_MESSAGES[chat_id] = message_id
        return result
    except APIError as exc:
        if "message is not modified" in str(exc).lower():
            LAST_BOT_MESSAGES[chat_id] = message_id
            return None
        raise


def refresh_screen(chat_id, text, keyboard=None):
    """Refresh the bot's last screen, or create a new one if it was deleted."""
    message_id = LAST_BOT_MESSAGES.get(chat_id)
    if message_id is not None:
        try:
            return edit_message(chat_id, message_id, text, keyboard)
        except APIError as exc:
            print(f"refresh edit failed for chat {chat_id}: {exc}", file=sys.stderr)
            LAST_BOT_MESSAGES.pop(chat_id, None)
    return send_fresh(chat_id, text, keyboard)
def answer_callback(query_id, text="", show_alert=False):
    try:
        tg("answerCallbackQuery", {
            "callback_query_id": query_id,
            "text": text,
            "show_alert": show_alert,
        })
    except Exception as exc:
        print(f"callback answer failed: {exc}", file=sys.stderr)


def is_admin(chat_id, user_id):
    try:
        member = tg("getChatMember", {"chat_id": chat_id, "user_id": user_id})
        return member and member.get("status") in {"administrator", "creator"}
    except Exception as exc:
        print(f"admin check failed: {exc}", file=sys.stderr)
        return False


def require_admin(chat_id, user_id):
    if is_admin(chat_id, user_id):
        return True
    send_fresh(chat_id, "⛔ <b>Admin only.</b>\nOnly Telegram group administrators can use the build bot.")
    return False


def is_owner(user_id):
    return str(user_id) == str(DELETE_OWNER_ID)


def stop_confirmation_keyboard():
    return [
        [{"text": "⛔ YES, STOP EVERYTHING", "callback_data": "stopconfirm"}],
        [{"text": "◀️ Back", "callback_data": "stopback"}],
    ]


def stop_confirmation_text():
    return (
        "<b>⚠️ STOP ZEPHYR BOT?</b>\n\n"
        "This will:\n"
        "• Disable new kernel builds\n"
        "• Disable the Telegram bot worker\n"
        "• Cancel active kernel builds\n"
        "• Cancel other active Telegram workers\n\n"
        "The GitHub Release workflow will remain available.\n\n"
        "Are you sure?"
    )


def _cancel_active_workflow_runs(workflow_id, exclude_run_id=None):
    states = {"queued", "in_progress", "waiting", "requested", "pending"}
    data = gh(
        "GET",
        f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(workflow_id, safe='')}/runs?per_page=100",
    )
    cancelled = 0
    for run in data.get("workflow_runs", []):
        run_id = run.get("id")
        if not run_id or str(run_id) == str(exclude_run_id):
            continue
        if run.get("status") not in states:
            continue
        try:
            gh("POST", f"/repos/{REPO}/actions/runs/{run_id}/cancel")
            cancelled += 1
        except Exception as exc:
            print(f"could not cancel workflow run {run_id}: {exc}", file=sys.stderr)
    return cancelled


def shutdown_zephyr():
    """Disable build/bot workflows, cancel their active runs, then stop this worker."""
    global STOP_REQUESTED
    current_run_id = os.environ.get("GITHUB_RUN_ID")
    results = {"disabled": [], "disable_errors": [], "build_cancelled": 0, "bot_cancelled": 0}

    # Disable first so scheduled/queued dispatches cannot create a new run
    # while we clean up currently active runs. GitHub returns 403 when asking
    # to disable a workflow that is already disabled, so check its state first.
    for workflow_id, label in ((BUILD_WORKFLOW, "kernel build"), (BOT_WORKFLOW, "Telegram bot")):
        try:
            workflow = gh(
                "GET",
                f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(workflow_id, safe='')}",
            )
            state = workflow.get("state")
            if state == "active":
                gh(
                    "PUT",
                    f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(workflow_id, safe='')}/disable",
                )
                results["disabled"].append(label)
            elif state == "disabled_manually":
                print(f"{label} workflow is already disabled.", flush=True)
            else:
                print(f"{label} workflow state is {state!r}; leaving unchanged.", flush=True)
        except Exception as exc:
            results["disable_errors"].append(f"{label}: {exc}")

    try:
        results["build_cancelled"] = _cancel_active_workflow_runs(BUILD_WORKFLOW)
    except Exception as exc:
        results["disable_errors"].append(f"kernel build cleanup: {exc}")

    try:
        results["bot_cancelled"] = _cancel_active_workflow_runs(BOT_WORKFLOW, current_run_id)
    except Exception as exc:
        results["disable_errors"].append(f"Telegram worker cleanup: {exc}")

    STOP_REQUESTED = True
    return results


def menu_text():
    return (
        "<b>⚡ ZEPHYR KERNEL BUILDER</b>\n\n"
        "📱 <b>Realme GT Neo 3</b> · zephyr / MT6895\n"
        "🧩 <b>Linux 5.10</b>\n\n"
        "Choose an action:"
    )


def menu_keyboard(user_id=None):
    rows = [
        [{"text": "🔨 Build Kernel", "callback_data": "build"}],
        [{"text": "📊 Current Build", "callback_data": "status"}],
    ]
    if is_owner(user_id):
        rows.append([{"text": "⛔ Stop Bot", "callback_data": "stopbot"}])
    return rows


def root_keyboard():
    return [
        [{"text": "🌱 KernelSU-Next", "callback_data": "root:ksu-next"}],
        [{"text": "🛡️ KernelSU", "callback_data": "root:kernel-su"}],
        [{"text": "🧬 SukiSU-Ultra", "callback_data": "root:sukisu-ultra"}],
        [{"text": "🐻 BakaSU", "callback_data": "root:baka-su"}],
        [{"text": "⚪ No Root", "callback_data": "root:none"}],
        [{"text": "❌ Cancel", "callback_data": "cancel"}],
    ]


def confirm_keyboard(root):
    return [
        [{"text": "🚀 START BUILD", "callback_data": f"confirm:{root}"}],
        [{"text": "✏️ Change", "callback_data": "build"}],
        [{"text": "❌ Cancel", "callback_data": "cancel"}],
    ]


def build_text(root):
    root_label = {"ksu-next": "KernelSU-Next", "kernel-su": "KernelSU", "sukisu-ultra": "SukiSU-Ultra", "baka-su": "BakaSU"}.get(root, "No Root")
    return (
        "<b>⚡ BUILD CONFIG</b>\n\n"
        f"🌱 Root · <code>{root_label}</code>\n"
        "📦 AnyKernel3 · <code>Enabled</code>\n\n"
        "Ready to build?"
    )


def active_run():
    data = gh(
        "GET",
        f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(BUILD_WORKFLOW, safe='')}/runs"
        f"?branch={urllib.parse.quote(BRANCH, safe='')}&per_page=20",
    )
    for run in data.get("workflow_runs", []):
        if run.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}:
            return run
    return None


def get_run(run_id):
    return gh("GET", f"/repos/{REPO}/actions/runs/{run_id}")


def run_config(run):
    name = str(run.get("name", "")).lower()
    if "· kernel-su ·" in name:
        return "kernel-su"
    if "· sukisu-ultra ·" in name:
        return "sukisu-ultra"
    if "· baka-su ·" in name:
        return "baka-su"
    if "· ksu-next ·" in name:
        return "ksu-next"
    return "none"


def refresh_tracked_screen(chat_id, item, text, keyboard):
    """Edit the tracked build card, or recreate it if the old card was deleted."""
    message_id = item.get("message_id")
    try:
        edit_message(chat_id, message_id, text, keyboard)
        return message_id
    except APIError as exc:
        print(f"tracked message refresh failed for chat {chat_id}: {exc}", file=sys.stderr)
        msg = send_fresh(chat_id, text, keyboard)
        new_id = msg.get("message_id") if isinstance(msg, dict) else None
        if new_id:
            item["message_id"] = new_id
        return new_id

def release_for_run(run_number):
    try:
        return gh("GET", f"/repos/{REPO}/releases/tags/zephyr-run{run_number}")
    except APIError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise


def successful_build_runs(limit=10):
    data = gh(
        "GET",
        f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(BUILD_WORKFLOW, safe='')}/runs"
        f"?branch={urllib.parse.quote(BRANCH, safe='')}&status=success&per_page={limit}",
    )
    return data.get("workflow_runs", [])


def dispatch_release(run_id, run_number):
    return gh(
        "POST",
        f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(RELEASE_WORKFLOW, safe='')}/dispatches",
        {
            "ref": BRANCH,
            "inputs": {
                "run_id": str(run_id),
                "run_number": str(run_number),
            },
            "return_run_details": True,
        },
    )


def dispatch_build(root):
    active = active_run()
    if active:
        return None, active

    gh(
        "PUT",
        f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(BUILD_WORKFLOW, safe='')}/enable",
    )

    result = gh(
        "POST",
        f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(BUILD_WORKFLOW, safe='')}/dispatches",
        {
            "ref": BRANCH,
            "inputs": {
                "root": root,
                "build_ak3": "true",
            },
            "return_run_details": True,
        },
    )

    run_id = result.get("workflow_run_id")
    if run_id:
        return gh("GET", f"/repos/{REPO}/actions/runs/{run_id}"), None

    data = gh(
        "GET",
        f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(BUILD_WORKFLOW, safe='')}/runs"
        f"?branch={urllib.parse.quote(BRANCH, safe='')}&per_page=10",
    )
    runs = data.get("workflow_runs", [])
    return (runs[0] if runs else None), None


def status_message(run):
    status = run.get("status", "unknown").replace("_", " ").title()
    conclusion = run.get("conclusion")
    if conclusion:
        status = f"{status} / {conclusion.title()}"
    return (
        "<b>📊 ZEPHYR BUILD</b>\n\n"
        f"🆔 Run <code>#{esc(run.get('run_number', '?'))}</code>\n"
        f"⚙️ <code>{esc(status)}</code>\n"
        "📱 GT Neo 3 · zephyr\n"
        "🧩 Linux 5.10 · MT6895"
    )


def status_keyboard(run):
    buttons = []
    if run.get("html_url"):
        buttons.append([{"text": "🔗 GitHub Actions ↗", "url": run["html_url"]}])
    buttons.append([{"text": "🔄 Refresh", "callback_data": "status"}])
    if run.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"} and run.get("id"):
        buttons.append([{"text": "🛑 Cancel Build", "callback_data": f"cancelrun:{run['id']}"}])
    elif run.get("id") and run.get("conclusion"):
        buttons.append([{"text": "🗑️ Delete Build", "callback_data": f"deletebuild:{run['id']}"}])
    return buttons


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def job_progress(run):
    data = gh("GET", f"/repos/{REPO}/actions/runs/{run['id']}/jobs?per_page=100")
    jobs = data.get("jobs", [])
    if not jobs:
        return "Starting runner…", 0, 1

    job = jobs[0]
    steps = job.get("steps") or []
    total = max(len(steps), 1)
    completed = sum(1 for s in steps if s.get("status") == "completed")
    current = next((s for s in steps if s.get("status") == "in_progress"), None)
    if current:
        name = current.get("name", "Working…")
        pct = min(99, int(((completed + 0.5) / total) * 100))
    elif job.get("status") in {"queued", "waiting", "requested", "pending"}:
        name = "Waiting for runner…"
        pct = 0
    else:
        name = steps[-1].get("name", "Finalizing…") if steps else "Starting…"
        pct = min(99, int((completed / total) * 100))

    labels = {
        "Checkout builder": "Preparing builder",
        "Install dependencies": "Installing dependencies",
        "Notify build started": "Starting build",
        "Install repo tool": "Preparing repo tool",
        "Sync Android kernel build manifest": "Syncing kernel source",
        "Inspect source tree": "Inspecting source",
        "Prepare Image.gz-only build config": "Configuring kernel",
        "Integrate KernelSU / KernelSU-Next": "Integrating KernelSU",
        "Build Image.gz": "Compiling Image.gz",
        "Verify Image.gz": "Verifying Image.gz",
        "Package AnyKernel3": "Packaging AnyKernel3",
        "Prepare release metadata": "Preparing release",
        "Create GitHub Release": "Publishing release",
        "Upload kernel artifacts": "Uploading artifacts",
        "Notify Telegram": "Finalizing",
    }
    return labels.get(name, name), pct, total


def elapsed_text(run):
    started = parse_time(run.get("run_started_at") or run.get("created_at"))
    if not started:
        return "--:--"
    seconds = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def progress_message(run, root, frame, cached_stage=None, cached_pct=None):
    if cached_stage is not None and cached_pct is not None:
        stage, pct = cached_stage, cached_pct
    else:
        try:
            stage, pct, _ = job_progress(run)
        except Exception as exc:
            print(f"progress query failed: {exc}", file=sys.stderr)
            stage, pct = "Checking build progress…", 0

    blocks = 8
    filled = min(blocks, max(0, int(round(pct / 100 * blocks))))
    bar = "█" * filled + "░" * (blocks - filled)
    spin = SPINNER[frame % len(SPINNER)]
    root_label = {"ksu-next": "KSU-Next", "kernel-su": "KernelSU", "sukisu-ultra": "SukiSU-Ultra", "baka-su": "BakaSU"}.get(root, "No Root")

    return (
        f"<b>⚡ KERNEL · BUILDING {spin}</b>\n\n"
        "📱 GT Neo 3 · zephyr\n"
        "🧩 Linux 5.10 · MT6895\n"
        f"🌱 {root_label}\n"
        "📦 <b>AnyKernel3</b>\n\n"
        f"{spin} <b>{esc(stage)}</b>\n"
        f"<code>[{bar}] {pct}%</code>\n"
        f"⏱ {elapsed_text(run)} · 🆔 #{esc(run.get('run_number', '?'))}"
    )


def publish_keyboard(run_id, run_number):
    return [
        [{"text": "🚀 Publish GitHub Release", "callback_data": f"publish:{run_id}:{run_number}"}],
        [{"text": "🧪 No — Actions only", "callback_data": f"skip:{run_id}:{run_number}"}],
    ]


def release_list_keyboard(runs):
    rows = []
    for run in runs[:8]:
        rows.append([{
            "text": f"🚀 Run #{run.get('run_number', '?')} · Publish",
            "callback_data": f"publish:{run.get('id')}:{run.get('run_number')}",
        }])
    return rows


def progress_keyboard(run):
    rows = [[{"text": "🔄 Refresh", "callback_data": "status"}]]
    if run.get("html_url"):
        rows.append([{"text": "🔗 GitHub Actions ↗", "url": run["html_url"]}])
    rows.append([{"text": "🛑 Cancel Build", "callback_data": f"cancelrun:{run['id']}"}])
    return rows


def track_build(chat_id, message_id, run, root):
    TRACKED_RUNS[chat_id] = {
        "run_id": run["id"],
        "message_id": message_id,
        "root": root,
        "frame": 0,
        "last_update": 0,
        "last_gh_update": 0,
        "stage": "Starting runner…",
        "pct": 0,
        "last_text": "",
    }


def monitor_builds():
    now = time.monotonic()
    for chat_id, item in list(TRACKED_RUNS.items()):
        if now - item["last_update"] < MONITOR_INTERVAL:
            continue
        item["last_update"] = now
        try:
            run = get_run(item["run_id"])
            if run.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}:
                if now - item.get("last_gh_update", 0) >= GH_PROGRESS_INTERVAL:
                    try:
                        item["stage"], item["pct"], _ = job_progress(run)
                    except Exception as exc:
                        print(f"progress query failed: {exc}", file=sys.stderr)
                    item["last_gh_update"] = now

                text = progress_message(
                    run,
                    item["root"],
                    item["frame"],
                    item.get("stage", "Starting runner…"),
                    item.get("pct", 0),
                )
                item["frame"] += 1
                if text != item["last_text"]:
                    refresh_tracked_screen(chat_id, item, text, progress_keyboard(run))
                    item["last_text"] = text
            else:
                TRACKED_RUNS.pop(chat_id, None)
        except Exception as exc:
            print(f"build monitor failed for chat {chat_id}: {exc}", file=sys.stderr)


def handle_message(message):
    chat = message.get("chat", {})
    user = message.get("from", {})
    chat_id = chat.get("id")
    user_id = user.get("id")
    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return

    command = text.split()[0].split("@")[0].lower()
    if command in {"/start", "/kernel"}:
        refresh_screen(chat_id, menu_text(), menu_keyboard(user_id))
    elif command == "/id":
        refresh_screen(
            chat_id,
            "<b>🆔 TELEGRAM USER ID</b>\n\n"
            f"User <code>{esc(user_id)}</code>",
            menu_keyboard(user_id),
        )
    elif command == "/release":
        if not require_admin(chat_id, user_id):
            return
        runs = []
        for run in successful_build_runs():
            if not release_for_run(run.get("run_number")):
                runs.append(run)
        target_id = LAST_BOT_MESSAGES.get(chat_id)
        if runs:
            text = "<b>📦 PUBLISH A BUILD</b>\n\nChoose a successful build to publish:"
            kb = release_list_keyboard(runs)
        else:
            text = "🟢 <b>No unpublished successful builds found.</b>"
            kb = menu_keyboard(user_id)
        if target_id:
            edit_message(chat_id, target_id, text, kb)
        else:
            send_fresh(chat_id, text, kb)
    elif command == "/status":
        tracked = TRACKED_RUNS.get(chat_id)
        run = None
        if tracked:
            try:
                run = get_run(tracked["run_id"])
            except Exception as exc:
                print(f"tracked run lookup failed: {exc}", file=sys.stderr)

        if run and run.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}:
            text = progress_message(run, tracked["root"], tracked["frame"])
            tracked["frame"] += 1
            refresh_tracked_screen(chat_id, tracked, text, progress_keyboard(run))
            tracked["last_text"] = text
        else:
            run = active_run()
            target_id = tracked["message_id"] if tracked else LAST_BOT_MESSAGES.get(chat_id)
            if run:
                root = run_config(run)
                if target_id:
                    text = progress_message(run, root, 0)
                    if tracked:
                        tracked["root"] = root
                        refresh_tracked_screen(chat_id, tracked, text, progress_keyboard(run))
                        target_id = tracked["message_id"]
                    else:
                        try:
                            edit_message(chat_id, target_id, text, progress_keyboard(run))
                        except APIError:
                            msg = send_fresh(chat_id, text, progress_keyboard(run))
                            target_id = msg["message_id"]
                    track_build(chat_id, target_id, run, root)
                else:
                    msg = send_fresh(
                        chat_id,
                        progress_message(run, root, 0),
                        progress_keyboard(run),
                    )
                    track_build(chat_id, msg["message_id"], run, root)
            else:
                refresh_screen(
                    chat_id,
                    "🟢 <b>No build is currently running.</b>",
                    menu_keyboard(user_id),
                )

def handle_callback(query):
    query_id = query.get("id")
    data = query.get("data", "")
    message = query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    user_id = (query.get("from") or {}).get("id")
    message_id = message.get("message_id")

    answer_callback(query_id, "Processing…")

    if not is_admin(chat_id, user_id):
        edit_message(chat_id, message_id, "⛔ <b>Admin only.</b>")
        return

    if data == "stopbot":
        if not is_owner(user_id):
            answer_callback(query_id, "Only the bot owner can stop Zephyr.", True)
            return
        edit_message(chat_id, message_id, stop_confirmation_text(), stop_confirmation_keyboard())
        return

    if data == "stopback":
        edit_message(chat_id, message_id, menu_text(), menu_keyboard(user_id))
        return

    if data == "stopconfirm":
        if not is_owner(user_id):
            answer_callback(query_id, "Only the bot owner can stop Zephyr.", True)
            return
        result = shutdown_zephyr()
        edit_message(
            chat_id,
            message_id,
            "<b>⛔ ZEPHYR BOT STOPPED</b>\n\n"
            "🛑 Kernel builds · <b>OFF</b>\n"
            "🛑 Telegram worker · <b>OFF</b>\n"
            f"🧹 Active builds cancelled · <b>{result['build_cancelled']}</b>\n"
            f"🧹 Other workers cancelled · <b>{result['bot_cancelled']}</b>\n\n"
            "GitHub Release remains available.\n\n"
            "To start again, re-enable and manually run the Telegram Bot workflow in GitHub Actions.",
            [],
        )
        return

    try:
        if data == "build":
            edit_message(chat_id, message_id, "<b>🌱 SELECT ROOT</b>\n\nChoose the root implementation:", root_keyboard())
            return

        if data == "root:ksu-next":
            edit_message(chat_id, message_id, "<b>⚡ BUILD CONFIG</b>\n\nKernelSU-Next selected.", confirm_keyboard("ksu-next"))
            return

        if data == "root:kernel-su":
            edit_message(chat_id, message_id, "<b>⚡ BUILD CONFIG</b>\n\nKernelSU selected.", confirm_keyboard("kernel-su"))
            return

        if data == "root:sukisu-ultra":
            edit_message(chat_id, message_id, "<b>⚡ BUILD CONFIG</b>\n\nSukiSU-Ultra selected.", confirm_keyboard("sukisu-ultra"))
            return
        if data == "root:baka-su":
            edit_message(chat_id, message_id, "<b>⚡ BUILD CONFIG</b>\n\nBakaSU selected.", confirm_keyboard("baka-su"))
            return

        if data == "root:none":
            edit_message(chat_id, message_id, "<b>⚡ BUILD CONFIG</b>\n\nNo root selected.", confirm_keyboard("none"))
            return

        if data.startswith("confirm:"):
            _, root = data.split(":", 1)
            if root not in {"ksu-next", "kernel-su", "sukisu-ultra", "baka-su", "none"}:
                raise APIError("invalid build selection")

            run, existing = dispatch_build(root)
            if existing:
                edit_message(chat_id, message_id, f"⚠️ <b>Build already running</b> · #{esc(existing.get('run_number', '?'))}", status_keyboard(existing))
                return

            if not run:
                edit_message(chat_id, message_id, "⚠️ <b>Build dispatched</b>\nGitHub has not created the run yet.", menu_keyboard(user_id))
                return

            root_label = {"ksu-next": "KSU-Next", "kernel-su": "KernelSU", "sukisu-ultra": "SukiSU-Ultra", "baka-su": "BakaSU"}.get(root, "No Root")
            rows = [[{"text": "🔄 Live Progress", "callback_data": "status"}]]
            if run.get("html_url"):
                rows.append([{"text": "🔗 GitHub Actions ↗", "url": run["html_url"]}])

            edit_message(
                chat_id, message_id,
                (
                    "<b>🚀 ZEPHYR · BUILD QUEUED</b>\n\n"
                    "📱 GT Neo 3 · zephyr\n"
                    "🧩 Linux 5.10 · MT6895\n"
                    f"🌱 {root_label} · 📦 AK3\n"
                    f"🆔 Run <code>#{esc(run.get('run_number', '?'))}</code>"
                ),
                rows,
            )
            track_build(chat_id, message_id, run, root)
            return

        if data == "status":
            tracked = TRACKED_RUNS.get(chat_id)
            run = None
            if tracked:
                try:
                    run = get_run(tracked["run_id"])
                except Exception as exc:
                    print(f"tracked refresh lookup failed: {exc}", file=sys.stderr)

            if run and run.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}:
                text = progress_message(run, tracked["root"], tracked["frame"])
                tracked["frame"] += 1
                edit_message(chat_id, tracked["message_id"], text, progress_keyboard(run))
                tracked["last_text"] = text
            else:
                run = active_run()
                if run:
                    root = run_config(run)
                    text = progress_message(run, root, 0)
                    try:
                        edit_message(chat_id, message_id, text, progress_keyboard(run))
                    except APIError:
                        msg = send_fresh(chat_id, text, progress_keyboard(run))
                        message_id = msg["message_id"]
                    track_build(chat_id, message_id, run, root)
                else:
                    edit_message(chat_id, message_id, "🟢 <b>No build is currently running.</b>", menu_keyboard(user_id))
            return

        if data.startswith("publish:"):
            _, run_id, run_number = data.split(":", 2)
            existing = release_for_run(run_number)
            if existing:
                release_url = existing.get("html_url", "")
                edit_message(
                    chat_id, message_id,
                    "<b>📦 RELEASE ALREADY PUBLISHED</b>\n\n"
                    f"Run <code>#{esc(run_number)}</code> is already released.",
                    [[{"text": "📦 GitHub Release ↗", "url": release_url}]] if release_url else menu_keyboard(user_id),
                )
                return
            dispatch_release(run_id, run_number)
            edit_message(
                chat_id, message_id,
                "<b>🚀 PUBLISHING RELEASE…</b>\n\n"
                f"Build Run <code>#{esc(run_number)}</code> is being published to GitHub Releases.",
                [[{"text": "🔗 Build Actions ↗", "url": f"https://github.com/{REPO}/actions/runs/{run_id}"}]],
            )
            return

        if data.startswith("skip:"):
            _, run_id, run_number = data.split(":", 2)
            edit_message(
                chat_id, message_id,
                "<b>🧪 ACTIONS-ONLY BUILD</b>\n\n"
                "Release not published. Test/download the artifact from the Actions run.",
                [
                    [{"text": "🧪 Actions Run / Download", "url": f"https://github.com/{REPO}/actions/runs/{run_id}"}],
                    [{"text": "🚀 Publish Later", "callback_data": f"publish:{run_id}:{run_number}"}],
                ],
            )
            return

        if data.startswith("deleteconfirm:"):
            if user_id != DELETE_OWNER_ID:
                answer_callback(query_id, "Only the bot owner can delete builds.", True)
                return
            run_id = data.split(":", 1)[1]
            gh("DELETE", f"/repos/{REPO}/actions/runs/{run_id}")
            TRACKED_RUNS.pop(chat_id, None)
            edit_message(
                chat_id, message_id,
                "🗑️ <b>BUILD DELETED</b>\n\n"
                "The GitHub Actions run and its Actions artifacts have been removed.",
                menu_keyboard(user_id),
            )
            return

        if data.startswith("deletebuild:"):
            if user_id != DELETE_OWNER_ID:
                answer_callback(query_id, "Only the bot owner can delete builds.", True)
                return
            run_id = data.split(":", 1)[1]
            run = get_run(run_id)
            run_number = run.get("run_number", "?")
            edit_message(
                chat_id, message_id,
                "⚠️ <b>DELETE BUILD?</b>\n\n"
                f"Run <code>#{esc(run_number)}</code> and its Actions artifacts will be permanently deleted.\n\n"
                "This does <b>not</b> delete a GitHub Release if you already published one.",
                [
                    [{"text": "🗑️ YES, DELETE", "callback_data": f"deleteconfirm:{run_id}"}],
                    [{"text": "↩️ Keep Build", "callback_data": "status"}],
                ],
            )
            return

        if data.startswith("cancelrun:"):
            run_id = data.split(":", 1)[1]
            gh("POST", f"/repos/{REPO}/actions/runs/{run_id}/cancel")
            TRACKED_RUNS.pop(chat_id, None)
            edit_message(chat_id, message_id, "🛑 <b>Build cancellation requested.</b>", menu_keyboard(user_id))
            return

        if data == "cancel":
            edit_message(chat_id, message_id, menu_text(), menu_keyboard(user_id))
            return

    except Exception as exc:
        print(f"callback {data!r} failed: {exc}", file=sys.stderr)
        edit_message(chat_id, message_id, f"❌ <b>Action failed</b>\n<code>{esc(exc)}</code>", menu_keyboard(user_id))


def schedule_next_worker():
    # workflow_dispatch is one of the events that GITHUB_TOKEN is allowed
    # to trigger, so the worker can hand off to a fresh runner before exit.
    try:
        gh(
            "POST",
            f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(BOT_WORKFLOW, safe='')}/dispatches",
            {"ref": BRANCH},
        )
        print("Queued next Telegram worker.", flush=True)
    except Exception as exc:
        print(f"Could not queue next Telegram worker: {exc}", file=sys.stderr)


def process_updates(updates):
    for update in updates:
        try:
            if "message" in update:
                handle_message(update["message"])
            elif "callback_query" in update:
                handle_callback(update["callback_query"])
        except Exception as exc:
            print(f"update {update.get('update_id', '?')} failed: {exc}", file=sys.stderr)


def main():
    print("Zephyr GitHub Telegram worker started", flush=True)

    try:
        tg("deleteWebhook", {"drop_pending_updates": False})
    except Exception as exc:
        print(f"deleteWebhook warning: {exc}", file=sys.stderr)

    # Long-lived worker: stay online for ~5h40m, safely below GitHub's 6h
    # GitHub-hosted job limit. A later scheduled/manual worker can take over.
    deadline = time.monotonic() + WORKER_SECONDS
    offset = 0

    while time.monotonic() < deadline and not STOP_REQUESTED:
        remaining = max(1, int(deadline - time.monotonic()))
        poll_timeout = min(POLL_TIMEOUT, remaining)

        try:
            updates = tg(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": poll_timeout,
                    "allowed_updates": ["message", "callback_query"],
                },
                timeout=poll_timeout + 10,
            ) or []
        except Exception as exc:
            print(f"Telegram polling failed: {exc}", file=sys.stderr)
            time.sleep(2)
            continue

        if updates:
            print(f"Processing {len(updates)} Telegram update(s).", flush=True)
            process_updates(updates)
            offset = max(update["update_id"] for update in updates) + 1

        try:
            monitor_builds()
        except Exception as exc:
            print(f"monitor loop failed: {exc}", file=sys.stderr)

    if not STOP_REQUESTED:
        schedule_next_worker()

    try:
        tg(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 0,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=5,
        )
    except Exception as exc:
        print(f"final Telegram confirmation failed: {exc}", file=sys.stderr)

    print("Telegram long-polling window finished.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())