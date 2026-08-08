# 常见问题排查

> 施工过程中遇到的真实问题按时间顺序往这里加,格式:现象 → 原因 → 处理方式。这份文档主要给未来的 AI Agent 和人类共同排障用,记录要具体(报错信息、命令、涉及的组件版本)。

## 索引

### kube-prometheus-stack 的 CRD 一直 OutOfSync,Prometheus 资源起不来

- **现象**:ArgoCD 里 `kube-prometheus-stack` Application 长期 `OutOfSync`,`kubectl get crd prometheuses.monitoring.coreos.com` 报 NotFound,Prometheus 的 Pod/StatefulSet 一直没创建出来。
- **原因**:prometheus-operator 的 CRD(尤其是 `prometheuses.monitoring.coreos.com`)体积很大,超过了 kubectl client-side apply 用来记录 `kubectl.kubernetes.io/last-applied-configuration` 的 annotation 大小上限(262144 字节),ArgoCD 默认走 client-side apply,导致这几个 CRD 应用失败。
- **处理**:给这个 Application 的 `syncPolicy.syncOptions` 加一条 `ServerSideApply=true`,改用 server-side apply 就不受这个限制。这是 kube-prometheus-stack + ArgoCD 组合已知的通用问题,不是本项目配置写错了,以后其他体积大的 CRD(比如某些 Operator)遇到同样症状,先怀疑这个。
- **涉及文件**:`platform/apps/kube-prometheus-stack.yaml`
