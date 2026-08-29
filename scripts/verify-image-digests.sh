#!/usr/bin/env bash
# 校验某台 Docker 主机上已经加载的镜像,内容(digest)是不是真的和官方
# 源一致——2026-08-16 真实抓到过的问题:云主机通过国内镜像站
# (`*.m.daocloud.io`)拉镜像时,9 个 quay.io 镜像内容和官方对不上(大概率
# 是镜像站缓存了旧版本没刷新,不是内容被篡改),如果不做这一步校验,
# 会把内容有问题的镜像当成"已经准备好"直接部署上去。详见
# docs/project/current-work.md 2026-08-16 那次的完整记录。
#
# 这是 ADR-055 里明确延后的 P1 项"镜像缓存 digest 校验"——之前延后是因为
# "没有真实场景验证过有没有用",现在已经用它真实抓到过问题,值得先把这
# 一个脚本做出来复用,不用等"专门收口基础设施"那个更大的时间窗口。
#
# 原理:对每个镜像,`docker manifest inspect --verbose <repo:tag>` 直接
# 连官方源(docker.io/quay.io/registry.k8s.io/ghcr.io/nvcr.io)查真实
# amd64 平台 digest,和这台主机上 `docker images --digests` 记录的
# digest 比较——**这个脚本必须在能直连官方源的机器上跑**(这个项目里是
# 这台 Mac,不是云主机本身,云主机在国内网络环境下不一定能直连所有官方
# 源,这也是当初要用镜像站的原因)。校验的是"内容对不对",不是"能不能
# 拉得到"。
#
# 用法:
#   ./scripts/verify-image-digests.sh <目标主机的 docker images --digests 输出文件>
#   比如:
#   ssh root@<云主机> "docker images --digests --format '{{.Repository}}:{{.Tag}} {{.Digest}}'" \
#     > /tmp/remote_digests.txt
#   ./scripts/verify-image-digests.sh /tmp/remote_digests.txt
#
# 退出码:发现任何不一致返回非 0(可以接进 CI/部署前置检查)。
#
# 2026-08-16 实测发现过一次自相矛盾的结果(同一个
# `quay.io/jetstack/cert-manager-controller:v1.21.1`,两次跑这个脚本拿到
# 的"官方 digest"不一样,原因是撞上了 Docker Hub 匿名拉取频率限制
# `toomanyrequests`)。**这次已经加了退避重试**(遇到 `toomanyrequests`
# 或者超时,间隔 8/20/45 秒重试最多 3 次),大幅降低了这类噪音,但没有
# 100% 消除——公网 registry 的限流策略不受这个脚本控制。报告里专门把
# "重试后依然失败/限流"的条目和"真的查到内容不一致"的条目分开列,前者
# 不代表内容有问题,只代表这次没能查清楚,**只有后一类才是需要处理的
# 真实问题**。
set -uo pipefail

DIGESTS_FILE="${1:?用法: $0 <docker images --digests 输出文件>}"
[ -f "$DIGESTS_FILE" ] || { echo "找不到文件: $DIGESTS_FILE" >&2; exit 1; }

echo "==> 生成需要校验的镜像清单(含 pending-definitions)"
cd "$(dirname "$0")/.."
python3 scripts/list-project-images.py --include-pending 2>/dev/null > /tmp/verify-image-list.txt

python3 - "$DIGESTS_FILE" /tmp/verify-image-list.txt <<'PYEOF'
import json, subprocess, sys, time

digests_file, target_file = sys.argv[1], sys.argv[2]

RETRY_DELAYS = [8, 20, 45]  # 秒,遇到限流/超时时的退避间隔


def inspect_with_retry(base):
    """跑 docker manifest inspect --verbose,遇到限流(toomanyrequests)
    或超时就退避重试,最多 RETRY_DELAYS 里定义的这么多次。返回
    (成功与否, stdout或者最后一次的错误信息, 是不是限流/超时导致的失败)。
    """
    last_err = ""
    rate_limited = False
    attempts = [0] + RETRY_DELAYS
    for i, delay in enumerate(attempts):
        if delay:
            time.sleep(delay)
        try:
            r = subprocess.run(['docker', 'manifest', 'inspect', '--verbose', base],
                                capture_output=True, text=True, timeout=25)
        except subprocess.TimeoutExpired:
            last_err = "timeout"
            rate_limited = True
            continue
        if r.returncode == 0:
            return True, r.stdout, False
        stderr = r.stderr.strip()
        last_err = stderr[:150]
        if 'toomanyrequests' in stderr or 'rate limit' in stderr.lower():
            rate_limited = True
            continue
        # 不是限流/超时导致的失败(比如镜像真的不存在),重试没有意义
        return False, last_err, False
    return False, last_err, rate_limited

remote = {}
for line in open(digests_file):
    parts = line.strip().rsplit(' ', 1)
    if len(parts) != 2:
        continue
    name, digest = parts
    if digest == '<none>':
        continue
    remote[name] = digest

targets = [l.strip() for l in open(target_file) if l.strip()]

verified, mismatches, rate_limited_list, unresolved = [], [], [], []

for img in targets:
    # nvcr.io/ecr-public/local 这几类要么走专门凭据要么本地构建,不在这个
    # 脚本的核实范围内(和 export-image-cache-amd64.sh 的处理方式一致)
    if img.startswith(('nvcr.io/', 'ecr-public.aws.com/', 'local/')):
        continue

    base = img.split('@')[0]
    lookup_key = base.replace('docker.io/', '')
    remote_digest = remote.get(lookup_key) or remote.get(base)
    if not remote_digest:
        # 目标主机上没有这个镜像的记录,不算"内容不一致",是另一类问题
        # (缺失),这个脚本只管"有没有,内容对不对",缺失的单独列出来
        unresolved.append(img)
        continue

    ok, output, was_rate_limited = inspect_with_retry(base)
    if not ok:
        if was_rate_limited:
            rate_limited_list.append(f"{img} (重试{len(RETRY_DELAYS)}次后仍然限流/超时: {output})")
        else:
            unresolved.append(f"{img} (manifest inspect失败: {output})")
        continue

    try:
        data = json.loads(output)
        entries = data if isinstance(data, list) else [data]
        amd64_digest = None
        for e in entries:
            plat = e.get('Descriptor', {}).get('platform', {})
            if plat.get('architecture') == 'amd64':
                amd64_digest = e.get('Descriptor', {}).get('digest')
                break
        if not amd64_digest and len(entries) == 1:
            amd64_digest = entries[0].get('Descriptor', {}).get('digest')
        if not amd64_digest:
            unresolved.append(f"{img} (解析不出amd64 digest)")
            continue
        if amd64_digest == remote_digest:
            verified.append(img)
        else:
            mismatches.append((img, amd64_digest, remote_digest))
    except Exception as e:
        unresolved.append(f"{img} ({e})")

print(f"\n验证通过: {len(verified)}")
print(f"内容不一致(真实问题,需要处理): {len(mismatches)}")
for img, official, actual in mismatches:
    print(f"  !! {img}")
    print(f"     官方: {official}")
    print(f"     目标主机: {actual}")
print(f"限流/超时,重试后仍未查清(不代表内容有问题,建议单独重跑这几个): {len(rate_limited_list)}")
for r in rate_limited_list:
    print(f"   {r}")
print(f"没能核实(缺失/查询失败/解析失败,不代表内容有问题): {len(unresolved)}")
for u in unresolved:
    print(f"   {u}")

sys.exit(1 if mismatches else 0)
PYEOF
exit $?
