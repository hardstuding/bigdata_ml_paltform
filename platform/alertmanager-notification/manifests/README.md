# 告警外部通知渠道

这个目录里的 YAML 是**条件生成**的:只有当 `environments/<env>/config.yaml`
里 `alert_notification_mode: webhook` 时,`render-environment-config.py` 才会
生成 `alertmanagerconfig-webhook.yaml`;其它模式下那个文件会被删掉。

源模板在 `templates/platform-alertmanager-notification/`,**不要直接改这个
目录里的生成产物**,会被下一次渲染覆盖。机制说明见
[ADR-060](../../../docs/decisions/060-conditional-rendering-and-tls-issuer.md)。

这份 README 本身不是 manifest(ArgoCD 的 directory 类型只认 YAML),它存在
的作用是**让这个目录在 git 里不为空**——空目录 git 不跟踪,目录消失会让
ArgoCD 报 "path does not exist"。
