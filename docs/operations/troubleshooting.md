# 常见问题排查

> 施工过程中遇到的真实问题按时间顺序往这里加,格式:现象 → 原因 → 处理方式。这份文档主要给未来的 AI Agent 和人类共同排障用,记录要具体(报错信息、命令、涉及的组件版本)。

## 索引

### kube-prometheus-stack 的 CRD 一直 OutOfSync,Prometheus 资源起不来

- **现象**:ArgoCD 里 `kube-prometheus-stack` Application 长期 `OutOfSync`,`kubectl get crd prometheuses.monitoring.coreos.com` 报 NotFound,Prometheus 的 Pod/StatefulSet 一直没创建出来。
- **原因**:prometheus-operator 的 CRD(尤其是 `prometheuses.monitoring.coreos.com`)体积很大,超过了 kubectl client-side apply 用来记录 `kubectl.kubernetes.io/last-applied-configuration` 的 annotation 大小上限(262144 字节),ArgoCD 默认走 client-side apply,导致这几个 CRD 应用失败。
- **处理(第一步,不够)**:给这个 Application 的 `syncPolicy.syncOptions` 加 `ServerSideApply=true`。**实测这一步不够** —— 即使开了 SSA,ArgoCD 在这几个 CRD 上还是会踩到同样的 "annotations too long" 校验错误(具体是 ArgoCD 内部哪个环节导致的还没深究,推测和它渲染/diff 时的某种 dry-run 行为有关)。
- **实际有效的处理**:把 CRD 从 ArgoCD 的管理范围里摘出去,单独用原生 `kubectl apply --server-side` 装:
  ```bash
  ./scripts/install-kube-prometheus-crds.sh
  ```
  然后在 chart 的 values 里设 `crds.enabled: false`,让 ArgoCD 只管 chart 本体(Deployment/Prometheus CR 等),不再插手 CRD 的创建。这是和 ArgoCD 本身、`platform/root-app.yaml` 一样的"允许手动执行"的例外(见 ADR-005),升级 chart 版本、CRD schema 变化时需要重新跑一遍这个脚本。
- **涉及文件**:`platform/apps/kube-prometheus-stack.yaml`、`scripts/install-kube-prometheus-crds.sh`
