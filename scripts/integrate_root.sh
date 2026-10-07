#!/usr/bin/env bash
set -euo pipefail

KERNEL_DIR="${1:?kernel source directory required}"
ROOT_IMPL="${2:-none}"
MANAGER_REF="${3:-}"

cd "$KERNEL_DIR"

if [ "$ROOT_IMPL" = "none" ]; then
  echo "==> Root integration disabled"
  exit 0
fi

if [ -z "$MANAGER_REF" ]; then
  echo "Manager release/tag is required for root integration"
  exit 1
fi

case "$ROOT_IMPL" in
  ksu-next|kernel-su|sukisu-ultra|baka-su) ;;
  *) echo "Unsupported root implementation: $ROOT_IMPL"; exit 1 ;;
esac

CONFIG_FRAGMENT="kernel/configs/oplus6895.config"

rm -rf KernelSU KernelSU-Next BakaSU

if [ "$ROOT_IMPL" = "ksu-next" ]; then
  KSU_REPO="https://github.com/KernelSU-Next/KernelSU-Next.git"
  KSU_DIR="KernelSU-Next"
  echo "==> Cloning KernelSU-Next"
  git clone --depth=1 --branch "$MANAGER_REF" "$KSU_REPO" "$KSU_DIR"
elif [ "$ROOT_IMPL" = "kernel-su" ]; then
  KSU_REPO="https://github.com/tiann/KernelSU.git"
  KSU_DIR="KernelSU"
  echo "==> Cloning KernelSU"
  git clone --depth=1 "$KSU_REPO" "$KSU_DIR"
elif [ "$ROOT_IMPL" = "sukisu-ultra" ]; then
  KSU_REPO="https://github.com/SukiSU-Ultra/SukiSU-Ultra.git"
  KSU_DIR="SukiSU-Ultra"
  echo "==> Cloning SukiSU-Ultra"
  git clone --depth=1 --branch "$MANAGER_REF" "$KSU_REPO" "$KSU_DIR"
else
  KSU_REPO="https://github.com/Baka-SU/BakaSU.git"
  KSU_DIR="BakaSU"
  echo "==> Cloning BakaSU"
  git clone --depth=1 --branch main "$KSU_REPO" "$KSU_DIR"
fi

if [ ! -f "$KSU_DIR/kernel/setup.sh" ]; then
  echo "KernelSU setup.sh not found"
  exit 1
fi

echo "==> Installing $ROOT_IMPL"
if [ "$ROOT_IMPL" = "sukisu-ultra" ]; then
  bash "$KSU_DIR/kernel/setup.sh" "$MANAGER_REF" main
else
  bash "$KSU_DIR/kernel/setup.sh"
fi

{
  echo
  echo "# Root implementation"
  echo "CONFIG_KSU=y"
  echo "CONFIG_KPROBES=y"
  echo "CONFIG_KPROBE_EVENTS=y"
} >> "$CONFIG_FRAGMENT"

echo "==> Verifying root integration"
test -d "$KSU_DIR/kernel"
grep -R "config KSU" -n "$KSU_DIR/kernel/Kconfig" 2>/dev/null || true
grep -E 'CONFIG_KSU=|CONFIG_KPROBES=|CONFIG_KPROBE_EVENTS=' "$CONFIG_FRAGMENT" || true