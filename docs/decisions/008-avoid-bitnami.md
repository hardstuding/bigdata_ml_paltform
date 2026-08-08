# 008. 全项目避开 Bitnami 镜像与 Helm chart

- 状态: 已采纳(2026-08-08)

## 背景

选 Keycloak 的 Helm chart 时发现:Bitnami(现属 Broadcom)已经把大部分容器镜像的持续更新和安全补丁收进商业订阅,公开镜像不再保证维护。这类"免费版突然变商业"的情况和 [ADR-001](001-kubernetes-colima.md) 里 OrbStack 的教训是同一类问题。

## 决策

项目里任何组件优先避开 Bitnami 系的镜像/Chart(`bitnami/*`、`bitnamicharts/*`)。优先级:

1. 组件官方维护的 Helm chart(如 cert-manager 用 jetstack 官方 chart、ingress-nginx 用 kubernetes 官方 chart)
2. CNCF / 项目社区维护的 chart(如 Prometheus 用 prometheus-community 的 kube-prometheus-stack)
3. 确实没有更好选择时,用活跃的第三方开源 chart,但要先确认它不是套壳 Bitnami 镜像(如 Keycloak 用 codecentric/keycloakx,直接基于官方 quay.io/keycloak 镜像)

## 后果

- 选型时多一步核实"这个 chart 背后的镜像来源",尤其是 Postgres、Redis、MinIO 这类常见组件历史上都有对应的 Bitnami 版本,容易顺手就用了。
- Postgres 在 cloud-full/prod 阶段用 CloudNativePG operator(本来就不是 Bitnami 路线,见 [ADR-004](004-environment-profiles.md)),local-lite 阶段用官方 postgres 镜像的单实例即可。
