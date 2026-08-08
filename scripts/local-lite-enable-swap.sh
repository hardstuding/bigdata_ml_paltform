#!/usr/bin/env bash
# 给本地 colima 虚拟机加 swap。只在 local-lite(这台 Mac)需要——真实的云/
# IDC 服务器内存充足,不会碰到这个问题。
#
# 背景:2026-08-08 同时部署 Kafka/Spark Operator/Airflow 把 VM 6GB 内存
# 打满(load average 36+),API server 失联,靠重启 colima 才恢复。VM 之前
# 没配 swap,内存一满就是硬崩,不会优雅降级。加上这个之后,压力大的时候
# 会变慢(换页到磁盘),而不是直接把 API server 拖死。
#
# 不是长期方案,是 local-lite 资源紧张下的缓冲垫——真正的解法是照着
# ADR-004 的 profile 设计,别在本地同时跑 cloud-full 才该跑的组件。
#
# 幂等:已经配过 swap 会跳过。
set -euo pipefail

SWAP_SIZE_GB="${1:-4}"

if colima ssh -- swapon --show 2>/dev/null | grep -q swapfile; then
  echo "swap 已经配置过,跳过。当前状态:"
  colima ssh -- free -h
  exit 0
fi

echo "==> 创建 ${SWAP_SIZE_GB}GB swap 文件"
colima ssh -- sh -c "
  sudo fallocate -l ${SWAP_SIZE_GB}G /var/lib/swapfile 2>/dev/null || sudo dd if=/dev/zero of=/var/lib/swapfile bs=1M count=\$((${SWAP_SIZE_GB} * 1024))
  sudo chmod 600 /var/lib/swapfile
  sudo mkswap /var/lib/swapfile
  sudo swapon /var/lib/swapfile
  grep -q '/var/lib/swapfile' /etc/fstab || echo '/var/lib/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
"

echo "==> 完成"
colima ssh -- free -h
