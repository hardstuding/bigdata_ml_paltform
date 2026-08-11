# 027. KServe 模型上线服务(Standard/RawDeployment 模式)

- 状态: 已采纳(2026-08-11,已验证:demo-rf-classifier 从 MLflow Model
  Registry 部署为真实 InferenceService,V2 协议推理请求返回随输入变化的
  分类结果)

## 决策

官方拆成两个 OCI chart 分开装:`oci://ghcr.io/kserve/charts/kserve-crd`
(CRD)和 `oci://ghcr.io/kserve/charts/kserve-resources`(controller +
webhook),`v0.19.0`。两个 Application 之间没有显式 sync wave,靠 ArgoCD
的 selfHeal 自动重试收敛,和 hive-metastore 等 Postgres 依赖是同一个模式
(见 troubleshooting.md)。

`deploymentMode: Standard`(官方术语也叫 RawDeployment),不用默认的
Knative Serverless 模式——避免再装一整套 Knative Serving(+ 通常还要
Istio)只为了拿 scale-to-zero,这台机器的资源画像用不上。Standard 模式下
InferenceService 落地成普通的 Deployment/Service,和项目里其他组件是同一套
心智模型。`gateway.ingressGateway.className` 指到已有的 ingress-nginx
(chart 默认是 istio)。

## 踩的坑 1:inferenceservices CRD 太大,client-side apply 超注解上限

`kserve-crd` Application 一直 `OutOfSync`,ArgoCD 报:

```
CustomResourceDefinition.apiextensions.k8s.io "inferenceservices.serving.kserve.io"
is invalid: metadata.annotations: Too long: may not be more than 262144 bytes
```

`inferenceservices` CRD 内嵌的 OpenAPI schema 太大,ArgoCD 默认走
client-side `kubectl apply`,会把整份 manifest 写进
`kubectl.kubernetes.io/last-applied-configuration` 注解,超过 k8s 单个
注解 262144 字节的硬限制。加 `syncOptions: [ServerSideApply=true]` 解决
——server-side apply 不写这个注解。这是 KServe 官方文档也建议的做法,不是
本地发明的绕过方式。

连带发现:`kserve-controller-manager` 因为拿不到这个 CRD(`no matches for
kind "InferenceService"`)反复崩溃重启,和 CRD 迟迟装不上是同一个根因,
CRD 修好后 controller 自愈。

## 踩的坑 2:kserve-resources chart 不带 ClusterServingRuntime

`kserve-resources` v0.19.0 起不再打包 sklearn/xgboost/mlserver 等
`ClusterServingRuntime` 资源——这些是 KServe 主仓库 `config/runtimes/`
下的静态 YAML,官方自己的安装方式(quick_install.sh)是单独
`kubectl apply -k` 这个目录,不归 Helm chart 管。ArgoCD 也没法管
(不是哪个 chart 的一部分,建 Application 意义也不大),走一次性手动脚本
`scripts/10-install-kserve-serving-runtimes.sh`,直接
`kubectl apply -k "https://github.com/kserve/kserve/config/runtimes?ref=v0.19.0"`
——宿主机能直连 GitHub,不需要走 CoreDNS 自定义 zone 或代理那一套(那些是
给集群内 pod 用的)。和 `scripts/04-install-kube-prometheus-crds.sh` 是
同一类"chart 管不了,手动脚本补"的模式。

## 踩的坑 3:MLflow 默认的 skops 序列化,serving 镜像的 mlflow 客户端不认

demo-rf-classifier 最早用 MLflow 3.x 默认的 `skops` 格式存的(比 pickle
安全,避免反序列化任意代码执行风险)。KServe 官方 sklearn runtime
(`kserve-sklearnserver`)只认 `model.joblib`,不认 MLflow 的目录结构;
能直接吃 MLflow 模型目录(`MLmodel` + 元数据)的是 `mlserver` runtime 的
`mlflow` 格式支持(`modelFormat.name: mlflow`)。但 mlserver 镜像
(`seldonio/mlserver:1.7.1`)自带的 mlflow 客户端是 2.22.1,报:

```
mlflow.exceptions.MlflowException: Unrecognized serialization format: skops.
Please specify one of the following supported formats: ['pickle', 'cloudpickle']
```

解法:`mlflow.sklearn.log_model(..., serialization_format="pickle")` 显式
换回 pickle。这是向部署目标妥协,不是否定 skops 更安全这个前提——以后
mlserver 镜像升级到支持 skops 的 mlflow 版本,应该改回默认。

## 踩的坑 4:pickle 格式对 sklearn 版本敏感,host 和 serving 容器版本不一致

换成 pickle 后第一次还是失败,报运行时(不是加载时)错误:

```
AttributeError: 'DecisionTreeClassifier' object has no attribute 'monotonic_cst'
```

pickle(和 cloudpickle)序列化的是 Python 对象本身,不像 skops/joblib 那样
做跨版本兼容处理。训练脚本跑在宿主机本地 Python(scikit-learn 1.3.2),
mlserver 镜像里是 1.7.0(`kubectl exec ... python3 -c "import sklearn;
print(sklearn.__version__)"` 确认),新版本的树模型预测代码路径会访问
老版本对象上不存在的属性。解法:本地 `pip install scikit-learn==1.7.0`
对齐镜像版本后重新训练。

**教训**:用 pickle/cloudpickle 部署 sklearn 模型时,训练环境和 serving
镜像的 scikit-learn 版本必须对齐,这是选择 pickle 而非 skops/joblib 时
要接受的额外约束,以后这条流水线如果要自动化,训练环境镜像应该固定成
和 mlserver 镜像同一个 scikit-learn 版本,而不是每次手动核对。

## 踩的坑 5:V2 推理协议的请求体格式,PandasCodec vs NumpyCodec

模型加载成功后,第一次推理请求报:

```
TypeError: float() argument must be a string or a real number, not 'InferenceRequest'
```

mlserver 的 V2 协议默认按 `PandasCodec` 解码请求(把每个具名 input 当一
个 DataFrame 列),而不是把单个 20 维数组直接喂给 `sklearn_model.predict`
期待的二维数组。解法:请求体加 `"parameters": {"content_type": "np"}`,
强制走 `NumpyCodec`,把 `inputs[0].data`(20 个数) reshape 成
`shape: [1, 20]` 的一个样本。验证请求示例:

```json
{
  "parameters": {"content_type": "np"},
  "inputs": [{"name": "input", "shape": [1, 20], "datatype": "FP64", "data": [...20 个数...]}]
}
```

两次用不同(互为相反数)输入验证,分别返回分类 0 和 1,确认是真实推理而
不是常量假成功。

## 后果

- demo-rf-classifier 部署在独立的 `kserve-demo` namespace,**不经过
  ArgoCD 管理**——这是一次性验证用的 demo 资源,不是平台基础设施,和
  `scripts/08-create-demo-data.sh` 建的 Superset demo dashboard 是同一类
  东西,通过 `scripts/11-deploy-demo-inference-service.sh` 一次性部署。
- 该 InferenceService 用的 `storageUri` 直接指向 MinIO 里 MLflow 存模型
  的路径(`s3://mlflow/2/models/<model_id>/artifacts`),不经过 MLflow
  服务本身——MLflow tracking server 验证完已经重新 park(见
  `environments/cloud-full/pending-definitions/mlflow.yaml`),KServe
  推理不依赖它,只依赖 MinIO 里的模型文件,和 ADR-023"训练任务直连
  MinIO/MLflow Service,不走 oauth2-proxy"是同一个道理。
- `scripts/train_demo_model.py` 现在显式用 `serialization_format="pickle"`
  而不是 MLflow 3.x 的默认 `skops`,这是为了兼容当前的 KServe serving
  路径,注释里已经写明如果以后升级 mlserver 镜像支持 skops,应该改回去。
- 没有配置 KServe 的 canary/traffic-splitting 做 A-B 测试(docs/architecture.md
  里"算法场景 A-B 测试用 KServe canary"这条还是待办,这次只验证了单一版本
  部署能跑通,金丝雀发布留到真正有多版本对比需求时再做)。
- ClusterServingRuntime 是集群级资源,`scripts/10-install-kserve-serving-runtimes.sh`
  装的十几个 runtime 本身不占资源(只有真正创建 InferenceService 引用某个
  runtime 时才会拉镜像起 Pod),可以放心一次性全装,不用按需精简。
