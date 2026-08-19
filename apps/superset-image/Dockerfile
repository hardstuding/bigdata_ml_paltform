# 官方 apache/superset 镜像不带 psycopg2-binary/authlib/trino 这三个包
# (见 apps/definitions/superset.yaml 顶部注释),chart 自带的
# bootstrapScript 机制靠容器启动时现场 `pip install` 补齐——这正是这个
# 项目已经踩过多次、明确记进 docs/BACKLOG.md P2.1 的"运行时装依赖"反模式:
# 每次 pod 重启都要重新联网装一遍,遇到这片网络的 PyPI 限速就直接卡死
# 探针预算(2026-08-16 真实故障:SIGKILL,5分44秒整,不是随机崩溃)。之前
# 只能靠调大 startupProbe 预算硬扛,没有从根上解决。
#
# 2026-08-19 改成和 apps/feast/feature-server-image、
# apps/argo-workflows-training-image 同一个模式:依赖在镜像构建时装好,
# 不在运行时装。构建产物本地留着(image-cache-amd64/),不止是这次省一次
# 网络等待——生产环境可能完全没有外网出口,这份本地缓存能直接
# `docker load` 用,不用现场再连一次网构建。
FROM apachesuperset.docker.scarf.sh/apache/superset:6.1.0

# 2026-08-19 真实踩坑 1:第一次构建用了普通 `pip install`,build 显示"成功"
# 但运行时 `import psycopg2` 还是 ModuleNotFoundError——查了官方
# /app/docker/pip-install.sh(chart 的 bootstrapScript 实际调的脚本)才
# 发现根因:这个镜像的 `python` 指向 `/app/.venv/bin/python`(一个 uv 建的
# venv,而且这个 venv 压根没打包 pip 模块本身),但 PATH 上的 `pip` 命令
# 解析到的是系统 Python 的 pip(`/usr/local/bin/pip`)——两者是不同的
# Python 环境,装到系统 Python 里的包,venv 里的 `python` 根本看不见。
# 官方脚本一直用的是 `uv pip install`(uv 会自动识别 VIRTUAL_ENV 装到
# 正确的 venv 里),照抄这个真正在用的机制,不是随便换个命令。
#
# 2026-08-19 真实踩坑 2:换成 `uv pip install` 后报 Permission denied——
# 镜像默认构建期用户是 superset(非 root,`docker inspect` 确认过
# Config.User=superset),但 `/app/.venv` 的 site-packages 目录属主是
# root,普通用户没有写权限。这个部署在运行时本来就是 uid 0 跑的(chart
# 的 bootstrapScript 里也写了"Running Superset with uid 0"),这里切
# root 只是让构建期权限和已有的运行时权限对齐,不是引入新的权限提升。
USER root
RUN uv pip install \
    --index-url https://mirrors.aliyun.com/pypi/simple/ \
    psycopg2-binary \
    authlib \
    trino
