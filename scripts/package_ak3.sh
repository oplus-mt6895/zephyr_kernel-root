#!/usr/bin/env bash
set -euo pipefail

KERNEL_DIR="${1:?kernel source directory required}"
OUT_DIR="${2:?build output directory required}"
ROOT_IMPL="${3:-ksu-next}"

IMAGE="$OUT_DIR/kernel-5.10/arch/arm64/boot/Image.gz"
[ -f "$IMAGE" ] || IMAGE="$OUT_DIR/../kernel-5.10/arch/arm64/boot/Image.gz"
[ -f "$IMAGE" ] || IMAGE="$KERNEL_DIR/arch/arm64/boot/Image.gz"
[ -f "$IMAGE" ] || { echo "Image.gz not found"; exit 1; }

mkdir -p artifacts
TS="$(date -u +%Y%m%d-%H%M)"
ROOT_LABEL="NoRoot"
[ "$ROOT_IMPL" = "ksu-next" ] && ROOT_LABEL="KSU-Next"
[ "$ROOT_IMPL" = "kernel-su" ] && ROOT_LABEL="KernelSU"
[ "$ROOT_IMPL" = "sukisu-ultra" ] && ROOT_LABEL="SukiSU-Ultra"
[ "$ROOT_IMPL" = "baka-su" ] && ROOT_LABEL="BakaSU"

MANAGER_URL="https://github.com/tiann/KernelSU/releases/tag/v3.0.0"
[ "$ROOT_IMPL" = "ksu-next" ] && MANAGER_URL="https://t.me/ksunext_ci"
[ "$ROOT_IMPL" = "sukisu-ultra" ] && MANAGER_URL="https://t.me/sukisuultra"
[ "$ROOT_IMPL" = "baka-su" ] && MANAGER_URL="https://github.com/Baka-SU/BakaSU/actions/workflows/build-manager.yml"
NAME="Zephyr-${ROOT_LABEL}-${TS}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ ! -d anykernel3 ]; then
  git clone --depth=1 https://github.com/almightygodthor/AnyKernel3.git anykernel3
fi

rsync -a --delete anykernel3/ "$WORK/"
cp "$IMAGE" "$WORK/Image.gz"
printf "%s\n" "$ROOT_LABEL" > "$WORK/ROOT"
printf "%s\n" "$MANAGER_URL" > "$WORK/MANAGER"

test -f "$WORK/anykernel.sh" || { echo "AnyKernel3 template missing"; exit 1; }

(
  cd "$WORK"
  zip -r9 "$OLDPWD/artifacts/${NAME}.zip" . -x '*.git*' >/dev/null
)
rm -f "artifacts/Image.gz"
printf '%s\n' "$NAME" > artifacts/BUILD_NAME
printf '%s\n' "$TS" > artifacts/BUILD_TIME_UTC
printf '%s\n' "$ROOT_LABEL" > artifacts/ROOT
printf '%s\n' "$MANAGER_URL" > artifacts/MANAGER
printf 'Created %s\n' "$NAME"
