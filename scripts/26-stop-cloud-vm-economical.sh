#!/usr/bin/env bash
# 停云主机,显式用"经济模式"(StoppedMode=StopCharging),确保停机期间
# 不继续按整机计费——2026-08-16 真实发现:这台实例的 `StoppedMode` 默认
# 是 `KeepCharging`(停机也照常收计算费),而且这个参数**只能在调用
# StopInstance API 的这一刻指定**,不是能一次设置永久生效的实例属性,
# 更不是虚拟机内部 `shutdown -h now` 能带得上的参数。
#
# 空闲自动关机那条路径由 scripts/24 安装的看门狗负责,它同样调这个 API
# 并带上 StopCharging(走 EcsRamRole 免密,实例上挂着
# `cloud-full-vm-self-stop` 这个角色,只有 StopInstance/DescribeInstances
# 权限、scope 到这一台)。这个脚本(26-)是**手动/立即想停机时**的快捷方式,
# 比如不想等 30 分钟的空闲判定。
#
# **这段注释 2026-09-03 更正过一次,原来写的是错的**,而且错得很贵:它当时
# 声称看门狗"已经"走 API、"不再依赖 shutdown -h now",但实际装到机器上的
# 脚本从头到尾都是 `/sbin/shutdown -h now`。后果是**每一次"自动关机省钱"
# 都没有省到** —— 从系统内部关机,阿里云记的是默认的 KeepCharging,停机
# 期间 CPU/内存照常按运行中收费。
#
# 顺带发现的第二层:那个 RAM 角色**在实例上根本没挂**。迁可用区时新实例
# 没把它带过来,所以即便脚本当初是对的,迁移之后也会静默失效。
#
# 教训不是"注释写错了",是**注释描述了一个没人验证过的状态**。要验证只需
# 一条命令,停机之后:
#     aliyun ecs DescribeInstances ... | grep StoppedMode
# StopCharging 才是真省了,KeepCharging 等于没停。
#
# 用法:
#   CLOUD_VM_INSTANCE_ID=<实例ID> CLOUD_VM_REGION=<地域> ./scripts/26-stop-cloud-vm-economical.sh
#   不传参数则用下面这两个默认值(这台机器现在唯一在用的 cloud-full 验证机)。
set -euo pipefail

# 实例身份来自 environments/cloud-full/vm.env(开机/停机/迁移共用一份,
# 见那个文件顶部的说明)。环境变量优先,方便临时指向别的实例。
_VM_ENV="$(dirname "$0")/../environments/cloud-full/vm.env"
[ -f "$_VM_ENV" ] && . "$_VM_ENV"
CLOUD_VM_INSTANCE_ID="${CLOUD_VM_INSTANCE_ID:-}"
# **读不到就停,不退回写死的值。** 兜底一个硬编码的实例 ID,意味着 vm.env
# 缺失时脚本会静默地对另一台(可能已经删掉的)实例动手 —— 停机脚本对着不
# 存在的实例"成功"返回,而真正在跑的机器一直烧钱。宁可报错。
if [ -z "${CLOUD_VM_INSTANCE_ID:-}" ]; then
  echo "!! 读不到实例 ID。应该来自 environments/cloud-full/vm.env," >&2
  echo "   或者用 CLOUD_VM_INSTANCE_ID=<id> 显式指定。" >&2
  exit 1
fi

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
