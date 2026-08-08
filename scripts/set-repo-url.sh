#!/usr/bin/env bash
# 迁移到 GitLab(个人的或公司的)、或者仓库改名/搬家时,把所有 ArgoCD
# Application 里硬编码的 git 仓库地址一次性换掉。
#
# 这些 YAML 里的 repoURL 目前指向 GitHub 的 demo 地址,ArgoCD 靠这个地址去
# 拉取本仓库里的 Application 定义和原始 manifest(app-of-apps 模式决定的,
# 不是 Helm chart 那种指向第三方 chart 仓库的 repoURL,那些不用改)。
#
# 用法:
#   ./scripts/set-repo-url.sh https://gitlab.com/<你的用户名>/bigdata_ml_paltform.git
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "用法: $0 <新的 git 仓库地址>" >&2
  echo "例如: $0 https://gitlab.com/yourname/bigdata_ml_paltform.git" >&2
  exit 1
fi

NEW_URL="$1"
OLD_URL="https://github.com/hardstuding/bigdata_ml_paltform.git"

FILES=$(grep -rl "$OLD_URL" --include="*.yaml" . || true)
if [ -z "$FILES" ]; then
  # 说明之前已经替换过一次,再找当前实际在用的旧地址不好猜,提示用户手动确认
  echo "没找到写死 ${OLD_URL} 的文件。如果这不是第一次运行这个脚本,"
  echo "说明地址已经换过了,自己确认一下 platform/root-app.yaml 里现在的 repoURL 对不对:"
  grep -n "repoURL" platform/root-app.yaml apps/root-app.yaml 2>&1 || true
  exit 0
fi

echo "$FILES" | while IFS= read -r f; do
  sed -i '' "s#${OLD_URL}#${NEW_URL}#g" "$f"
  echo "已更新: $f"
done

echo
echo "改完记得: git add -A && git commit -m 'chore: 迁移仓库地址' && git push"
echo "然后重新跑一遍 scripts/02-bootstrap-root-apps.sh(或者等 ArgoCD 自动检测到 push)。"
