# ADR-091:Superset 的告警与报表,是一整条链,不是一个开关

日期:2026-09-02
状态:**已实现并实机验证(通知已真实投递,收到的 payload 逐字段核对过)**

## 问题

2026-09-02 使用方抽查 Superset,第一句话是"告警的菜单好像暂时也没有"。

菜单确实没有。但把 `ALERT_REPORTS` 开出来并不能解决问题 —— 那只会让
情况从"看不见"变成"看得见但建了永远不触发",**更难查**:界面上一切正常,
告警列在那里,状态是"未触发",而没有任何地方告诉你它根本不会被执行。

这条链完整是这样的:

```
特性开关 ALERT_REPORTS          ← 决定菜单在不在
  └─ Celery beat                ← 每分钟把到期的告警投进队列
      └─ Redis(broker)          ← 队列本身
          └─ Celery worker      ← 真正执行:查数据源、判条件、发通知
              └─ 通知实现        ← 决定收件端收到的是什么形状
```

**任何一环缺失,表现都是"告警不工作",但现场看不出缺的是哪一环。**

## 决定

五环全部补齐,并且每一环都用产物验证,不看状态。

### 1. 独立的 Redis 作为 broker

新增 `apps/superset-redis/`,不复用别的组件的 Redis。理由和
`apps/feast/manifests/redis.yaml` 那次一样:broker 里堆的是任务,和缓存
的失效策略、内存压力完全不同,共用一个实例会互相影响。没有 PVC ——
告警任务丢了就丢了,下一分钟 beat 会重新投。

配置上有个坑:chart 的 `cache.enabled: false` 会把 `REDIS_HOST` 渲染成
字面量 `unused`,而且 `superset-db-secrets` 那个手工建的 Secret 里也
硬编码了一份 `REDIS_HOST=unused` —— **同一个值有两个来源**,只改一处
不生效。两处都改了,并在 `00-generate-secrets.sh` 里记了原因。

### 2. beat 和 worker 的配额、并发

- **不给 `resources` 会落到命名空间 LimitRange 的默认 128Mi**,Superset
  worker 起步就要几百 MB,直接 OOMKilled —— 而表现是 CrashLoopBackOff,
  要 `describe` 才看得到 OOMKilled。
- **给够内存也还是 OOM**:celery 默认 `--concurrency` = CPU 核数,这台机器
  16 核,于是起 16 个 prefork 子进程,每个完整加载一份 Superset。
  日志里那行 `concurrency: 16 (prefork)` 才是根因,**加内存治不了**。
  按环境分档限并发(local-lite 1 / cloud-full 2 / prod 4)。
- 命名空间的 ResourceQuota 是按"只有一个 Superset pod"配的,加了三个
  组件之后 Pod **建都建不出来**(`get pods` 里什么都没有,只有
  `describe deploy` 的 ReplicaFailure 说得出原因)。这个坑这个仓库栽第
  四次了,判据写进了 `platform/resource-quotas/manifests/quotas.yaml`。

### 3. CeleryConfig 不要自己写第二份

chart 自带的 `superset_config.py` 里已经有一份完整的 CeleryConfig,而且
它的 `imports` 比手写的多了 `superset.tasks.thumbnails` 和
`superset.tasks.cache`。自己再写一份的后果是同一个文件里两个同名 class,
**后一个静默覆盖前一个**,thumbnails 的 import 就此丢掉 —— 当场不报错,
但"告警带截图"会在某天突然不工作,而现场没有任何线索。

### 4. 通知实现:注册顺序决定谁生效

企微通知那段二开(ADR-090)写完之后**一行都没跑过**。
`create_notification()` 遍历 `BaseNotification.plugins` 返回**第一个**
类型匹配的,而上游的 `WebhookNotification` 在 import 时就已经注册,子类
排在后面永远轮不到。修法是显式把父类摘掉、子类插到最前。

**这个错完全静默**:告警发出去了、执行日志是 Success、没有任何异常 ——
只有去看接收端收到的到底是什么形状才发现得了。

## 验证(2026-09-02,cloud-full)

在 `superset` 命名空间起了一个一次性的 HTTP sink 接收 webhook,建了一条
每分钟执行的告警(数据源 Trino,`SELECT 1 > 0`),然后逐环核对:

| 环节 | 证据 |
|---|---|
| beat 在投任务 | `report_execution_log` 每分钟一条新记录 |
| worker 在执行 | 记录状态从 Working 走到终态,worker 日志有对应堆栈 |
| 条件判定跑了 | 执行走到了 `_get_notification_content`(要发通知了) |
| 通知真的发出去 | sink 收到 502 字节的 POST |
| 二开生效 | 核对收到的 payload 形状(第一次核对**没通过**,见上面第 4 条) |

**中途暴露的两个"配了但不能用"**:

1. 告警必须关联一个看板或图表。不关联的话执行会在拼"在 Superset 里
   查看"那个链接时抛 `'NoneType' object has no attribute 'id'` ——
   报错信息完全指不到真正的原因。UI 上这是必填项,所以只影响用脚本建的。
2. **镜像里没有 headless 浏览器**(只有 selenium 库,没有 chromedriver/
   chromium)。所以 `report_format` 是 PNG/PDF 的告警会卡在 Working 直到
   `working_timeout`,不会报错。当前把验证用的告警设成 TEXT 跑通了链路;
   带截图的告警需要给 worker 换一个带 chromium 的镜像 ——
   见 `docs/project/production-readiness-gaps.md`,这条没有掩盖成"已支持"。

## 代价

- 多了三个常驻 pod(redis + worker + beat),cloud-full 上约 2.3Gi 内存。
- 告警的最小粒度是 1 分钟(beat 的 `reports.scheduler` 频率)。
- 带截图的告警现在不可用,**如实记在能力矩阵里**,不算已交付。
