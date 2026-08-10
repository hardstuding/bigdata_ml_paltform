# 022. CI:push/PR 前跑 `helm template` 拦一部分配置错误

- 状态: 已采纳(2026-08-10)

## 背景

这次session里好几个坑(Kafka chart 版本写错、`livenessProbe` 覆盖字段名不对、
Trino 证书路径嵌套挂载失败……)都是 push 到 git、ArgoCD 同步之后才在集群里
报错发现的,来回排查成本不低。这些坑里,有一部分(chart 版本不存在、
values 字段名拼错、YAML 缩进错误)其实本地跑一次 `helm template` 就能提前
拦住,不需要真的部署到集群。

## 决策

- **`scripts/validate-charts.py`**:扫描 `platform/apps/`、
  `apps/definitions/`、`environments/cloud-full/pending-definitions/`
  下所有 ArgoCD Application,对每个 Helm chart 来源跑 `helm template`,
  渲染失败就报错退出。纯 git manifest 来源(没有 chart 字段)的
  Application,退而求其次对它引用的 manifests 目录做 YAML 语法检查
  (`helm template` 用不上,但至少能挡住手滑打错缩进这种低级错误)。
  和 `scripts/list-project-images.py` 是同一个"扫描所有 Application 配置"
  的思路,但目的不同(一个是收集镜像清单,一个是验证渲染成功与否),没有
  强行合并成一个工具——两者关心的失败模式不一样,合并会让接口变复杂。
- **`.github/workflows/validate.yml`**:push 到 main 和开 PR 时跑
  `scripts/validate-charts.py`。

## 明确拦不住什么

`helm template` 只验证"chart 语法 + 我们的 values 能不能正确渲染成合法的
YAML",**不验证渲染出来的配置在真实集群里跑不跑得起来**。这次踩的很多坑
本质上都是这一类,CI 拦不住:
- `livenessProbe` 打错端口(chart 硬编码,不是渲染错误,渲染完全正常,
  是运行时行为不对)
- Secret 的 key 名字对不上(渲染时能通过,运行时才报 `not found`)
- Postgres 密码漂移(和渲染完全无关,是集群里已有状态和新配置对不上)
- cert-manager 自签证书没写 `commonName` 导致 Java 解析器拒绝(渲染出来的
  YAML 完全合法,是应用层对证书内容的额外校验)

这些"运行时才暴露"的问题,CI 目前没有覆盖,唯一办法还是真的部署到一个
真实集群里验证——这也是为什么 local-lite 这套"能跑起来的本地环境"本身
仍然是不可替代的,CI 只是减少"明显能提前发现的错误"消耗的排查时间,不是
取代真实环境验证。

## 后果

- CI 目前只跑 `helm template`,没有更进一步的 kubeconform(校验渲染出来的
  manifest 是否符合 k8s API schema,比如 apiVersion 写错、必填字段缺失)
  或者真的起一个临时集群做 smoke test——这些如果以后配置错误的模式往这个
  方向集中,可以再加,现在先把最简单、成本最低的这一层加上。
- `environments/cloud-full/pending-definitions/` 里的组件平时不在
  local-lite 跑,但 CI 每次都会验证它们的 chart 渲染——这是刻意的:这些
  组件"配置已验证过,收起来省内存",不代表以后改配置就不用管了,CI 覆盖
  这些文件能防止以后修改 pending 组件的配置时引入新的渲染错误却没人发现。
