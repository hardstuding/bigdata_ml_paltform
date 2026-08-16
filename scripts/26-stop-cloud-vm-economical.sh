#!/usr/bin/env bash
# 停云主机,显式用"经济模式"(StoppedMode=StopCharging),确保停机期间
# 不继续按整机计费——2026-08-16 真实发现:这台实例的 `StoppedMode` 默认
# 是 `KeepCharging`(停机也照常收计算费),而且这个参数**只能在调用
# StopInstance API 的这一刻指定**,不是能一次设置永久生效的实例属性,
# 更不是虚拟机内部 `shutdown -h now` 能带得上的参数。
#
# 之前装的看门狗脚本(scripts/24-install-idle-shutdown-watchdog.sh)是在
# 虚拟机内部执行 `shutdown -h now` 触发关机,天生拿不到经济模式——真正
# 想拿到"停机不计费",必须从外部调这个 API。理想方案是给虚拟机挂一个
# 权限受限的"实例 RAM 角色"让它自己能这么做,但这次 zhenghe 给的
# AccessKey 权限范围只到 ECS 操作,没有 RAM/角色相关权限,做不了那个
# 方案(如果以后想做,需要额外授权 ram:CreateRole 等权限)。
#
# 现阶段的实际做法:看门狗继续负责"检测有没有人在用"这件事(这个能力
# 还是有价值,能在日志里看到判断记录),但**真正执行关机这个动作,由我
# 主动从这边(有 aliyun CLI 权限的这台 Mac)调用这个脚本完成**,不依赖
# 虚拟机自己关自己——这样保证每次都是经济模式,不会漏计费优化。
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
