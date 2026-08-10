# 023. 训练任务连 MLflow/MinIO:直接打集群内部 Service,不走 oauth2-proxy

- 状态: 已采纳(2026-08-10,已验证:真实训练一个 sklearn 模型,记录实验、注册模型,Registry API 确认存在)

## 背景

MLflow 前面挂了 oauth2-proxy 做 Keycloak SSO(ADR-019)。这解决的是"人在
浏览器里看实验/模型"这个场景,但训练任务(`scripts/train_demo_model.py`)
是一个本地跑的 Python 脚本,不是浏览器,没有办法完成 oauth2-proxy 的
Authorization Code 交互式登录流程——和 Trino 服务账号(ADR-021)是同一类
"人类走 SSO、服务到服务走不了这条路"的问题。

## 决策

不给训练任务单独搞一套认证机制,直接让它绕过 oauth2-proxy,连 MLflow 和
MinIO 的集群内部 Service(`mlflow-mlflow.mlflow.svc.cluster.local:5000`、
`minio.minio.svc.cluster.local:9000`),本机通过 `kubectl port-forward`
接进来(`scripts/09-train-demo-model.sh` 负责建/收这两个 port-forward)。

这样做是因为 oauth2-proxy 本身只是**挂在 Ingress 前面的一层**,它保护的是
"从集群外部访问这个域名"这条路径,MLflow 自己的 Service
(`mlflow-mlflow`)从来没有在自己身上加认证——集群内部任何知道这个
Service 地址的东西都能直接连,这不是绕过安全机制,是这个架构从一开始
就有的、本来就存在的访问路径,和 Trino 需要专门建一个服务账号
(Trino 自己的认证是全局强制的,所有端口都要认证)不是同一种情况。

## 后果

- 这个"信任集群内部网络"的模式在 local-lite 阶段没问题(没有
  NetworkPolicy,所有 pod 之间默认互通),cloud-full/prod 如果要收紧
  ("只有指定的训练任务命名空间能连 MLflow"这类需求),需要靠
  NetworkPolicy 或者服务网格的授权策略,不是靠给 MLflow 加认证层
  ——给 MLflow 本身加认证会同时挡住集群内部合法的训练任务流量,方向
  不对。
- 这次训练任务是在本机(Mac)跑的,通过 `kubectl port-forward` 接进
  集群,不是真的"集群内部"发起的请求——生产环境的训练任务(比如以后
  接 JupyterHub/Argo Workflows)应该是真的跑在集群里的 Pod,直接用
  Service 的集群内域名连,不需要 port-forward 这一层,这次的
  port-forward 只是本地开发阶段的权宜之计。
- `scripts/train_demo_model.py` 依赖的 Python 包(`mlflow-skinny`、
  `scikit-learn`、`skops`、`boto3`)是装在本机 Python 环境里的,不在
  `scripts/list-project-images.py` 管理的容器镜像清单范围内——这是本机
  开发工具链的依赖,不是集群里跑的东西,两者是不同的依赖管理范畴,故意
  不混在一起。
