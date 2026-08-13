# 安全策略

## 报告漏洞

如果你在这个项目里发现安全问题(比如:凭据/密钥处理不当、权限模型
可以被绕过、依赖组件的已知 CVE 在这里的用法下真的可被利用),**不要**
开公开 Issue 或 PR 描述里直接贴细节。

请通过 GitHub 的 [Private vulnerability reporting](../../security/advisories/new)
(仓库页面 Security 标签下)提交,或者直接联系仓库维护者。请尽量提供:

- 具体是哪个组件、哪份配置文件
- 复现步骤,或者为什么这是一个真实可利用的问题(不是纯理论上的担心)
- 影响范围:只影响 `local-lite`(本机开发环境),还是 `cloud-full`/
  `prod` 画像下也成立

## 这个项目当前的安全假设,报告问题前可以先看一眼

这些是已知的、有意识的取舍,不是遗漏——除非你的报告指出了这些假设
本身站不住脚,否则不需要重复报告:

- **`local-lite` 画像的权限模型**:单机开发验证环境,能直接访问这台
  机器(`kubectl`/`colima ssh`)就等同于集群管理员,没有做任何隔离。
  见 [`docs/operations/onboarding-offboarding.md`](docs/operations/onboarding-offboarding.md)
  最后一节,这是已知的、明确记录过的、只适用于 local-lite 阶段的限制,
  `cloud-full`/`prod` 阶段需要重新设计。
- **`*.local-lite.test` 用的是自签证书**(见 [ADR-016](docs/decisions/016-ingress-domains-local-lite.md)),
  只在本机开发环境生效,不代表 `prod` 画像也会用自签证书——见
  [`environments/prod/README.md`](environments/prod/README.md)。
- **本地生成的初始密码/密钥存在 `secrets/generated-credentials.txt`**
  (这个文件本身在 `.gitignore` 里,不会被提交),这是 local-lite 阶段
  的权宜方案,不代表 prod 阶段也应该用明文文件管理密钥。

## 依赖组件

这个项目大量复用官方维护的开源组件(Keycloak、ArgoCD、Trino、
CloudNativePG 等,见 [ADR-008](docs/decisions/008-avoid-bitnami.md)
"只用官方支持的部署方式"这条原则),版本锁定情况见
[`docs/operations/upgrade.md`](docs/operations/upgrade.md)。这些
组件自身的 CVE 应该优先上报给对应上游项目;如果怀疑这个仓库里的具体
用法/配置方式放大了某个上游 CVE 的影响面(比如默认配置暴露了不该暴露
的端口),欢迎按上面的流程报告。
