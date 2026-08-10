# 021. Trino 服务账号:OAUTH2 + PASSWORD 并存,给 Superset 用

- 状态: 已采纳(2026-08-10)

## 背景

ADR-017 把 Trino 的认证方式换成了 Keycloak OAuth2(Authorization Code 模式)。
这个模式是给人在浏览器里操作设计的:跳转登录页、输入密码、回调换 token。
但 Superset 的 SQL Lab 要把 Trino 当一个普通数据源连——每次有人在 Superset
里跑一条 SQL,是 Superset 后端替他去连 Trino,不是那个人的浏览器直接连,
这中间没有"人在浏览器里走一遍 OAuth2 登录"这个环节,Authorization Code
模式在这个场景里用不上。

## 决策

给 Trino 加一个并存的 PASSWORD 认证方式,只给 Superset 这一个服务账号用,
人类继续走 Keycloak OAuth2:

- `http-server.authentication.type=OAUTH2,PASSWORD`——Trino 原生支持多种
  认证方式同时配置,服务端按顺序尝试,不是"只能选一个"。客户端发 HTTP
  Basic Auth 的用户名密码,自然会在 PASSWORD 这个 authenticator 上验证
  通过,不会被强行塞进 OAuth2 流程,两条路互不干扰。
- 密码文件用 Trino 自带的 file password authenticator(bcrypt,cost 至少
  8——这是 Trino 文档写的硬性最低要求),不是自己发明的认证机制。
- 服务账号叫 `superset_service`,只给 Superset 用,不给人用——如果以后
  别的服务(比如某个批处理脚本)也要连 Trino,应该建一个自己的服务账号,
  不要复用这一个,方便以后追溯"是谁的流量"、方便单独吊销。
- 密码/哈希文件生成和管理都在 `scripts/00-generate-secrets.sh` 里(用系统
  自带的 `htpasswd -B` 生成 bcrypt 哈希,不是自己实现哈希算法),`trino`
  命名空间生成后复制一份给 `superset` 命名空间(和 MinIO/Postgres 凭据
  跨命名空间复制是同一个模式)。

## 后果

- 这是给"BI 工具需要服务账号连一个开了人类 SSO 的后端"这类场景的通用解法,
  不是 Trino/Superset 专属——以后如果有别的组件也遇到类似情况(某个后端
  服务要连一个已经启用了浏览器 SSO 的另一个组件),可以参考同一个思路:
  查这个后端支不支持"多种认证方式并存",而不是假设"开了 SSO 就必须所有
  访问都走 SSO"。
- `superset_service` 这个账号目前是 Trino 系统里唯一的密码认证用户,权限
  和通过 OAuth2 登录的人类用户一样(local-lite 阶段没有做精细的按用户
  权限区分,见 ADR-006 的"用真实需求驱动"原则)。cloud-full/prod 阶段如果
  需要限制这个服务账号只能查特定的 catalog/schema,需要另外配置 Trino 的
  访问控制(`access-control.properties`),这次没做。
- 密码文件用 `file.refresh-period`(默认 5s)支持热更新,轮换密码不需要
  重启 Trino,但目前没有自动轮换机制,轮换需要手动重新生成
  `trino-service-account` 这个 Secret。
