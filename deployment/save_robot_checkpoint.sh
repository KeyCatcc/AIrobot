#!/usr/bin/env bash
# Snapshot deployed sources/configuration; does not stop or control hardware.
set -euo pipefail
checkpoint_root=/home/cat/rai
checkpoint_stamp=$(date +%Y%m%d-%H%M%S)
checkpoint_dir="$checkpoint_root/checkpoints/pre-audio2face-$checkpoint_stamp"
mkdir -p "$checkpoint_dir"
chmod 700 "$checkpoint_dir"
checkpoint_targets=(robot-action-runtime releases/20261110-ai)
if [[ -d "$checkpoint_root/robot-control-ws" ]]; then
  checkpoint_targets+=(robot-control-ws)
fi
tar -czf "$checkpoint_dir/deployed-code.tar.gz" \
  --exclude=.git --exclude=.venv --exclude=.venv-ai --exclude=__pycache__ \
  --exclude=build --exclude=install --exclude=log --exclude=logs \
  --exclude='*.pyc' --exclude='*.env' --exclude='.env*' \
  --exclude='密钥*' --exclude='*secret*' --exclude='*credential*' \
  --exclude='config.toml' \
  -C "$checkpoint_root" "${checkpoint_targets[@]}"
# Keep credentials/config TOMLs in a separate owner-only archive on the robot.
# Do not upload or share private-config.tar.gz.
checkpoint_private=()
for checkpoint_file in .robot_action.env .duplex_control.env .rai_cloud.env \
  releases/20261110-ai/rai-main/config.toml \
  releases/20261110-ai/python3.7_duplex_demo/python3.7_duplex_demo/config.toml; do
  if [[ -f "$checkpoint_root/$checkpoint_file" ]]; then
    checkpoint_private+=("$checkpoint_file")
  fi
done
if ((${#checkpoint_private[@]})); then
  (umask 077; tar -czf "$checkpoint_dir/private-config.tar.gz" \
    -C "$checkpoint_root" "${checkpoint_private[@]}")
fi
{
  date -Is
  uname -a
  /usr/bin/python3 --version
  printf '\nROS_DISTRO=%s\n' "${ROS_DISTRO:-unset}"
  printf '\nCompatibility path:\n'
  readlink -f /home/cat/xmov_new || true
} > "$checkpoint_dir/platform.txt"
if [[ -x "$checkpoint_root/.venv-ai/bin/python" ]]; then
  "$checkpoint_root/.venv-ai/bin/python" -m pip freeze > "$checkpoint_dir/ai-requirements.txt" || true
fi
tar -tzf "$checkpoint_dir/deployed-code.tar.gz" > "$checkpoint_dir/archive-files.txt"
(cd "$checkpoint_dir"; sha256sum *.tar.gz > SHA256SUMS; sha256sum -c SHA256SUMS)
printf '\nSaved checkpoint: %s\n' "$checkpoint_dir"
printf 'Keep private-config.tar.gz on the robot; it contains credentials.\n'
