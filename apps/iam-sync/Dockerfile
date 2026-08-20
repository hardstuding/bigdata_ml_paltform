# 见 docs/decisions/031-iam-auto-sync-cronjob.md 和 docs/BACKLOG.md 2.1。
# 之前这个 CronJob 每次跑都要在 initContainer 里装 kubectl(apt 走
# pkgs.k8s.io 镜像)、在主容器里装 git + pyyaml——运行时装工具这件事本身
# 才是这个镜像要解决的问题,不是"要不要每次都拉最新的 platform/iam/ 数据"
# (那部分是这个 CronJob 存在的意义,必须保留在运行时,见 cronjob.yaml 里
# `git clone` 那一步的说明)。这个 Dockerfile 只把"装 git/kubectl/pyyaml
# 这几个工具"这件事挪到构建期,不动"clone 仓库拿最新 IAM 数据"这件事。
#
# kubectl 版本跟着构建时的 stable release 走(探测 dl.k8s.io/release/
# stable.txt 拿 minor 版本号,再装对应版本的官方包),不锁死成一个固定
# 版本号——这个仓库的 k3s 版本本身也在滚动升级,kubectl 客户端只要保持在
# 同一个大版本线内,不需要精确匹配。
#
# **不换阿里云镜像源**,这点和这个仓库其他 Dockerfile/运行时脚本不一样,
# 是故意的:那些地方之所以要换镜像站,是因为构建/运行动作发生在
# cloud-full(阿里云 ECS,直连境外源限速到几十 KB/s)或者本机 colima 上。
# 这个镜像改成由 GitHub Actions 构建(见 .github/workflows/
# build-images.yml),CI runner 在境外,直连官方源(deb.debian.org、
# pypi.org、dl.k8s.io、pkgs.k8s.io)反而更快更稳,换成阿里云镜像源实测
# 在这个网络环境下连不上(`Unable to connect to mirrors.aliyun.com`)。
# 用官方源是更简单也更符合实际网络环境的选择,不是漏配。
FROM python:3.12-slim@sha256:876416ecde9aca2bcc90e1fb0c7a9500bbf749f5788b70f82d4c5a5c2357f8b4

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends git curl gnupg ca-certificates \
    && KUBECTL_MINOR=$(curl -fsSL https://dl.k8s.io/release/stable.txt | sed -E 's/^(v[0-9]+\.[0-9]+)\..*/\1/') \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL "https://pkgs.k8s.io/core:/stable:/${KUBECTL_MINOR}/deb/Release.key" | gpg --dearmor -o /etc/apt/keyrings/k8s.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/k8s.gpg] https://pkgs.k8s.io/core:/stable:/${KUBECTL_MINOR}/deb/ /" > /etc/apt/sources.list.d/kubernetes.list \
    && apt-get update -qq \
    && apt-get install -y -qq kubectl \
    && apt-get purge -y -qq curl gnupg \
    && rm -rf /var/lib/apt/lists/* /etc/apt/sources.list.d/kubernetes.list /etc/apt/keyrings/k8s.gpg \
    && pip install --no-cache-dir pyyaml==6.0.3
