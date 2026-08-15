# 当前唯一主任务

> 这份文档解决的问题(2026-08-15 Codex review 第二轮指出的):这次会话
> 在门户/OPA/dbt/cloud-full 部署/本机内存/回应外部 review 之间来回切换,
> 没有一个清晰的"现在到底在做哪一件事"锚点。规则很简单:**任何时候只有
> 一个 CURRENT,新想法默认进 `docs/BACKLOG.md`,不自动抢占 CURRENT**。
> 每次恢复工作先看这份文档,不要只信聊天记录/记忆摘要。

## CURRENT

- **标题**:cloud-full 环境(阿里云)部署上线
- **为什么现在做**:local-lite 本机资源已经到物理上限(16GB Mac),
  用户确认生产是 x86_64,需要一个和生产架构一致、资源充足的环境完成
  Trino OPA 真实权限闭环、dbt/SeaTunnel 端到端验证等本机做不完的事。
- **明确范围**:把 `environments/cloud-full/pending-definitions/` 里的
  组件收回常驻、跑通 ArgoCD、完成"从零拉起整套服务"流程,达到和
  local-lite 同等的核心链路验证水平。
- **明确非目标**(这些不属于当前主线,出现新想法先记
  `docs/BACKLOG.md`):Trino OPA 真正切换生效(需要用户在场,单独排期)、
  P1 工程收口(环境 overlay 重构/自建工具补测试/扩大 CI)、5 条产品主线
  (统一开发工作台等)、任何新组件/新功能。
- **当前阶段**:镜像缓存传输中(见下面"正在运行的任务")
- **详细进度/实例信息**:见 `environments/cloud-full/STATUS.md`(这份
  文档不重复那些细节,只负责"现在主线是什么、下一步做什么")
- **计费资源状态**:阿里云 ECS 按量付费,当前开机中,详见 STATUS.md
- **验收标准**:`kubectl get applications -n argocd`(cloud-full 集群)
  全部 Synced/Healthy;核心链路(Trino 查询、Superset 出图、Airflow 跑
  一次 DAG)至少各验证一次
- **最后更新**:2026-08-15

## 正在运行的后台任务

(用 Bash 工具的 `run_in_background` 机制管理,任务 ID 是这个工具自己
分配的,不是额外自建的 task-runner——这个项目目前是单 Claude 会话操作,
Bash 工具自带的后台任务追踪+完成通知已经够用,没有必要再建一套平行机制)

- 本地 amd64 镜像导出(`scripts/export-image-cache-amd64.sh`):跑在
  这台 Mac 上,输出到 `image-cache-amd64/`,进度看
  `logs/export-image-cache-amd64.log` 或者
  `wc -l image-cache-amd64/manifest.txt` 对比 68 这个总数。
- SSH 隧道(`ssh -f -N -L 16443:127.0.0.1:6443 ...`):常驻后台进程,
  给 `KUBECONFIG=~/.kube/cloud-full-config` 用,断了要重新起。
- 增量传输+加载(`scripts/22-load-image-cache-remote.sh`):每导出一批
  本地镜像就传一批到云端,不用等 68 个全部导出完才开始传,云主机不空转。

如果你是接手这个工作的人(人类或者别的 AI):先跑
`./scripts/cloud-full-preflight.sh`(设置 `CLOUD_VM_IP`/`CLOUD_VM_KEY`)
看现在是不是 READY,不要凭猜测判断进度。

## 下一步唯一动作

镜像缓存导出+传输完成后,跑 `scripts/01-bootstrap-argocd.sh`(装
ArgoCD,不加 `NEEDS_LOCAL_PROXY`),然后按
`environments/cloud-full/STATUS.md` 的"进度清单"继续。

## 结束一段工作前必须确认(照着过一遍,不要跳)

- [ ] `git status` 干净,该 push 的都 push 了
- [ ] 计费资源现在的状态说清楚了(开着/停了,为什么)
- [ ] 后台任务/SSH 隧道是不是还开着,写进了上面那节
- [ ] 这次做的事,哪些是真实验证过的、哪些只是写完代码没测,分层说清楚
- [ ] 有没有手工改过集群但没回写 git 的操作(有的话赶紧记下来或者补写)
- [ ] 失败但没解决的事情,写清楚现象+已经排除的原因,别人接手不用重新排查一遍
