#!/usr/bin/env bash
# 停云主机,显式用"经济模式"(StoppedMode=StopCharging),确保停机期间
# 不继续按整机计费——2026-08-16 真实发现:这台实例的 `StoppedMode` 默认
# 是 `KeepCharging`(停机也照常收计算费),而且这个参数**只能在调用
# StopInstance API 的这一刻指定**,不是能一次设置永久生效的实例属性,
# 更不是虚拟机内部 `shutdown -h now` 能带得上的参数。
#
# 2026-08-16 已经补上了 RAM 实例角色方案:`cloud-full-vm-self-stop`
# (仅有 ecs:StopInstance/DescribeInstances/DescribeInstanceStatus 权限,
# scope 到这一台实例的 ARN)挂在实例上,看门狗脚本
# (scripts/24-install-idle-shutdown-watchdog.sh 安装的
# /usr/local/bin/idle-shutdown-watchdog.sh,不进 git)自己会用
# `aliyun ecs StopInstance --mode EcsRamRole ... --StoppedMode
# StopCharging` 从虚拟机内部调这个 API,不再依赖本地 `shutdown -h now`
# ——所以自动空闲关机现在本来就是经济模式,不需要人/Claude 守在这台 Mac
# 上才能触发。
#
# 这个脚本(26-)的用途因此收窄成:**手动/立即想停机时**的快捷方式(比如
# 不想等 30 分钟空闲判定,或者看门狗那条路径万一失败需要兜底重试),不再
# 是达成经济模式的唯一途径。
#
# 用法:
#   CLOUD_VM_INSTANCE_ID=<实例ID> CLOUD_VM_REGION=<地域> ./scripts/26-stop-cloud-vm-economical.sh
#   不传参数则用下面这两个默认值(这台机器现在唯一在用的 cloud-full 验证机)。
set -euo pipefail

CLOUD_VM_INSTANCE_ID="${CLOUD_VM_INSTANCE_ID:-i-0jlbped4h1959tp591pe}"
CLOUD_VM_REGION="${CLOUD_VM_REGION:-cn-wulanchabu}"
ALIYUN_PROFILE="${ALIYUN_PROFILE:-cloud-full}"

cd "$(dirname "$0")/.."
mkdir -p logs
LOG_FILE="logs/stop-cloud-vm-economical.log"

echo "=== $(date -u +%FT%TZ) 停止实例 ${CLOUD_VM_INSTANCE_ID}(${CLOUD_VM_REGION}),经济模式 ===" >> "$LOG_FILE"

current_status=$(aliyun ecs DescribeInstances --profile "$ALIYUN_PROFILE" \
  --InstanceIds "[\"${CLOUD_VM_INSTANCE_ID}\"]" 2>>"$LOG_FILE" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['Instances']['Instance'][0]['Status'])")

if [ "$current_status" != "Running" ]; then
  echo "实例当前状态是 ${current_status},不是 Running,不需要(也没法)停止。" | tee -a "$LOG_FILE"
  exit 0
fi

aliyun ecs StopInstance --profile "$ALIYUN_PROFILE" \
  --InstanceId "$CLOUD_VM_INSTANCE_ID" \
  --StoppedMode StopCharging \
  2>&1 | tee -a "$LOG_FILE"

echo "已发起停止(经济模式)。确认最终状态:"
sleep 5
aliyun ecs DescribeInstances --profile "$ALIYUN_PROFILE" --InstanceIds "[\"${CLOUD_VM_INSTANCE_ID}\"]" 2>>"$LOG_FILE" \
  | python3 -c "import json,sys; print('状态:', json.load(sys.stdin)['Instances']['Instance'][0]['Status'])"
