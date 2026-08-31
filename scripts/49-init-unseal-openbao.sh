#!/usr/bin/env bash
# OpenBao 初始化 + 解封(ADR-089)。**幂等**:每次开机都会跑,重复跑安全。
#
# OpenBao 每次重启都是**封印**状态,要用解封密钥解开才能服务。这台云主机
# 是竞价实例、经常关机重开 —— 每次开机都要人工解封的话,"一键拉起"这条
# 底线就不成立了。所以这个脚本进部署主线,开机也会跑一次。
#
# **它做的事分两种,要分清楚:**
#
#   初始化(operator init)  只在**第一次**做。产出 unseal key + root token,
#                           **这两样丢了数据就永远打不开了**,所以立刻存进
#                           k8s Secret。
#   解封(operator unseal)  每次重启都要做。
#
# **这一档的取舍,说清楚**:unseal key 存在同一个集群的 k8s Secret 里 ——
# 拿到 Secret 读权限的人能解开 OpenBao,静态加密的意义因此大打折扣。
# 这是 `seal_mode: dev-autounseal` 那一档明确接受的代价,prod 那档用云 KMS
# (`seal_mode: kms`),`scripts/check-prod-secrets-ready.py` 会拦住占位值。
#
# 用法:
#   ./scripts/49-init-unseal-openbao.sh
#
# 日志:logs/49-openbao-<时间>.log(照这个仓库的约定,重要操作留日志)
set -euo pipefail

NS="${OPENBAO_NAMESPACE:-openbao}"
POD="${OPENBAO_POD:-openbao-0}"
KEYS_SECRET="${OPENBAO_KEYS_SECRET:-openbao-unseal-keys}"
# 解封需要的份数 / 总份数。3-of-5 是默认;这一档反正都存在同一个 Secret 里,
# 分片数量不增加任何实际安全性,**但也不减少** —— 保持默认,免得以后换成
# 真正分发给不同人保管时还要改这里。
SHARES="${OPENBAO_KEY_SHARES:-5}"
THRESHOLD="${OPENBAO_KEY_THRESHOLD:-3}"

mkdir -p logs
LOG_FILE="logs/49-openbao-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== OpenBao 初始化/解封 $(date -u +%FT%TZ) ==="

echo "==> 等 Pod 起来(封印状态下 readiness 是 false,所以等的是 Running 不是 Ready)"
for i in $(seq 1 60); do
  phase=$(kubectl -n "$NS" get pod "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || true)
  [ "$phase" = "Running" ] && break
  [ "$i" = 60 ] && { echo "!! 等了 10 分钟 $NS/$POD 还没 Running"; exit 1; }
  sleep 10
done

# `bao status` 在封印时退出码是 2,未初始化时是 2,正常是 0 —— 不能用
# `set -e` 直接跑,也不能把非零一律当失败。
status_json() { kubectl -n "$NS" exec "$POD" -- bao status -format=json 2>/dev/null || true; }

initialized=$(status_json | python3 -c 'import json,sys
try: print(str(json.load(sys.stdin)["initialized"]).lower())
except Exception: print("unknown")')

if [ "$initialized" = "unknown" ]; then
  echo "!! 读不到 OpenBao 状态,Pod 可能还在启动。再等 60 秒重试一次"
  sleep 60
  initialized=$(status_json | python3 -c 'import json,sys
try: print(str(json.load(sys.stdin)["initialized"]).lower())
except Exception: print("unknown")')
  [ "$initialized" = "unknown" ] && { echo "!! 仍然读不到状态,人工查:kubectl -n $NS logs $POD"; exit 1; }
fi

if [ "$initialized" = "false" ]; then
  if kubectl -n "$NS" get secret "$KEYS_SECRET" >/dev/null 2>&1; then
    # **这是最危险的一种状态,必须停下来。**
    # Secret 在但 OpenBao 说没初始化,意味着数据卷被换掉/清空了,而 Secret
    # 里还是老的密钥。这时候如果闷头再 init 一次,老密钥会被覆盖 ——
    # 万一原来的数据卷还能找回来,就再也解不开了。
    echo "!! ${NS}/${KEYS_SECRET} 已存在,但 OpenBao 说未初始化。"
    echo "   这通常意味着数据卷被换掉或清空了,而 Secret 里还是老密钥。"
    echo "   **不自动 init** —— 那会覆盖老密钥,让原数据(如果还找得回来)"
    echo "   永远打不开。请人工确认后再决定:"
    echo "     - 数据确实不要了:kubectl -n $NS delete secret $KEYS_SECRET,再跑本脚本"
    echo "     - 数据还要:先把原来的 PVC 挂回来"
    exit 1
  fi
  echo "==> 第一次初始化(${THRESHOLD}-of-${SHARES})"
  init_json=$(kubectl -n "$NS" exec "$POD" -- bao operator init \
    -key-shares="$SHARES" -key-threshold="$THRESHOLD" -format=json)
  # **立刻存盘,存不进去就当没初始化过一样报错退出** —— 这几行如果失败,
  # 密钥只存在于这个 shell 的变量里,窗口一关就永久丢失。
  echo "$init_json" | NS="$NS" KEYS_SECRET="$KEYS_SECRET" python3 -c '
import json, os, subprocess, sys

d = json.load(sys.stdin)
keys = d["unseal_keys_b64"]
args = ["kubectl", "-n", os.environ["NS"], "create", "secret", "generic",
        os.environ["KEYS_SECRET"],
        "--from-literal=root_token=" + d["root_token"]]
for i, k in enumerate(keys):
    args.append("--from-literal=unseal_key_%d=%s" % (i, k))
subprocess.run(args, check=True)
print("已把 root token 和 %d 份 unseal key 存进 Secret" % len(keys))
' || {
      echo "!! 存密钥失败。**密钥现在只在内存里,必须立刻手工保存**:"
      echo "$init_json"
      exit 1
    }
  echo "已初始化。密钥在 ${NS}/${KEYS_SECRET}"
else
  echo "==> 已经初始化过,跳过 init"
fi

sealed=$(status_json | python3 -c 'import json,sys
try: print(str(json.load(sys.stdin)["sealed"]).lower())
except Exception: print("unknown")')

if [ "$sealed" = "false" ]; then
  echo "==> 已经是解封状态,不用做什么"
else
  echo "==> 解封(需要 ${THRESHOLD} 份)"
  for i in $(seq 0 $((THRESHOLD - 1))); do
    key=$(kubectl -n "$NS" get secret "$KEYS_SECRET" -o jsonpath="{.data.unseal_key_$i}" | base64 -d)
    if [ -z "$key" ]; then
      # 明确报出来。空值传给 unseal 只会报一句含糊的解析错,而真正的原因是
      # Secret 里少了这一份 —— 比如有人手工改过它。
      echo "!! ${NS}/${KEYS_SECRET} 里没有 unseal_key_$i,解不了封"
      exit 1
    fi
    # **`-` 表示从 stdin 读密钥,不放命令行参数。** 命令行参数会出现在
    # Pod 里的 `ps` 和 kubectl 的审计日志里,而这是能解开整个凭据库的东西。
    kubectl -n "$NS" exec -i "$POD" -- bao operator unseal - >/dev/null <<< "$key"
    echo "  已用第 $((i + 1))/${THRESHOLD} 份"
  done
  sealed=$(status_json | python3 -c 'import json,sys
try: print(str(json.load(sys.stdin)["sealed"]).lower())
except Exception: print("unknown")')
  [ "$sealed" = "false" ] || { echo "!! 解封后状态仍然是 sealed=$sealed,人工查"; exit 1; }
  echo "已解封"
fi

echo
echo "=== 完成。日志:$LOG_FILE ==="
echo "看 root token(排障用,平时不需要):"
echo "  kubectl -n $NS get secret $KEYS_SECRET -o jsonpath='{.data.root_token}' | base64 -d"
