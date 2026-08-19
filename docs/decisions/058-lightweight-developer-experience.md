# 058. 开发者体验:用"薄 SDK + 脚手架 + skill"替代自建平台 UI

- 状态: **设计已定,尚未实现**。这份 ADR 记录方向和边界,实现分批做,
  每批完成后回来更新状态。

## 背景

### 用户提出的真实问题

2026-08-19 晚,zhenghe 在真实使用这套环境(JupyterHub / Airflow /
Superset / OpenMetadata 都已部署并登录验证过)之后,提出了一组连贯的
产品问题,原话摘录:

> "结合 git 的开发形式问题。你看我那个 algo,我连接 jupyter 后,就用的是
> 服务器上的环境,通过 git 提交代码来实现代码版本管理和代码同步,然后
> 通过自己封装了一个方法,就可以等同使用服务器环境,来触发调度。
> **因为现在大都要借助 AI,所以我感觉我现在这套用着还行,本地 ide 等都
> 可以直接复用 AI 编程工具的能力,过度平台化后,我感觉都没办法这么方便
> 的使用环境。**"

> "像我那个 algo 项目里的,如果管理员开发了一些通用代码(utils),有办法
> 让每个人都能使用吗?同时 airflow 里也能用等同的环境?"

> "**想要帮助使用者简化使用,又想要能够减少自己的二开,避免平台开发投入
> 过多,不好维护和升级**"

> "可能可以借鉴一下,但是如果自己搭平台太重,可能还是通过 skill,或者
> 封装一些简易的脚手架可能会好一点。"

这三点合起来定义了这份 ADR 的约束:**要简化使用、要保住本地 AI 编程
工具的能力、要少写代码**。三者中任何一条被牺牲,方案就是错的。

### 参考系一:zhenghe 自己的生产项目 `ysb/algo`

这不是假想的需求,是一套真实跑了几年的工作方式。读代码确认的事实
(`/Users/zhenghe/my_work/ysb/algo`,不是凭印象):

- `utils` 是**独立 git repo**(`git@remote.ysbang.cn:algo/utils.git`),
  clone 到 `~/git/algo/utils`,靠 `PYTHONPATH` 生效——没有打包流程、
  没有私有 registry、没有版本发布。
- `utils/requirements/` 下 **21 个 conda 环境定义**,按用途拆
  (`spark.yml` / `tf20.yml` / `llm.yml` / `rag.yml` / `mlflow.yml` /
  `dolphinscheduler.yml` …),用 `conda env export/create` 管理。
- `$EXECUTENOTEBOOK <kernel> <notebook.ipynb> --arg v` 是调度执行的
  入口,那个 `kernel` 参数就是**选用哪个 conda 环境**
  (`utils/common_utils/execute_notebook.py`:把 notebook 转成带时间戳
  和 uuid 的 `.py`,再起子进程执行)。
- `utils/Makefile` 把 `common_utils/spark_utils/stats_utils/all_config`
  打成 `python_libs.zip`,通过 Spark `--py-files` 分发到 executor,
  保证 executor 上也能 import 同一套 utils。
- 调度用 DolphinScheduler,workflow 本身也是写在 notebook 里的 Python
  代码(`pydolphinscheduler` 的 `Workflow`/`Shell`,用 `>>` 串依赖)。

**这套东西为什么好用,一句话:环境是共享的、代码走 git、交互开发和调度
执行用的是同一个环境,平台没有插在人和环境中间。** zhenghe 担心的
"过度平台化",准确说就是担心平台插进第三条。这个担心是合理的,不是保守。

### 参考系二:Google Cloud 的做法(zhenghe 主动提到)

> "我感觉谷歌等的一些云平台好像都有实现了一些比较好的方法了"

对照 Vertex AI Workbench / Colab Enterprise / Vertex AI Pipelines:

| GCP 的机制 | 解决的问题 | 对应 algo 里的 |
|---|---|---|
| Runtime template(管理员定义镜像+机型,用户下拉选) | 环境策展 | 21 个 conda env |
| 同一个 custom container 既跑 notebook 又跑 CustomJob | 交互/调度环境一致 | `$EXECUTENOTEBOOK <kernel>` |
| Artifact Registry 私有 pip 包 | 共享 utils | `utils` repo + PYTHONPATH |
| GCS + gcsfuse | 团队共享文件 | 服务器共享目录 |
| KFP DSL(Python 写 → 编译 → 提交) | 流水线即代码 | pydolphinscheduler |

**最值得学的不是它做了什么,是它没做什么**:GCP 没有为了"简化"去做一个
web IDE 来取代用户本地的 IDE。它把复杂度收敛在**镜像 + SDK + registry**
这三件事上,IDE 那层交还给用户(本地 VSCode/Cursor 随便)。这正是 AI 编程
时代该有的形态,也正好是 zhenghe 要的。

### 这个平台当前的真实差距

`docs/usage-guide.md` "交互式开发 / Notebook" 一节已经如实记录:
**没有"打开 notebook 就自动连好 Trino、自动带着自己权限"这种体验**,
用户要自己装 client、自己填连接串和账号密码。这是本 ADR 要解决的
第一个具体问题。

(注:该节还写着 JupyterHub 处于 park 状态,这条已过时——2026-08-19
已部署并真实登录验证通过,实现本 ADR 时要一并更正那段描述。)

## 决策

### 核心原则:一份代码,三处运行,差异只在环境变量

同一个 `.py` 文件,在**本地 IDE**、**JupyterHub notebook**、**调度器
任务 pod** 里都能不加修改地跑,唯一的区别是环境变量的值。

**这不是新发明的原则,是当天已经验证过的事实**:
`scripts/train_demo_model.py` 被 `scripts/09-train-demo-model.sh`
(本机 port-forward,`MLFLOW_TRACKING_URI=http://localhost:15500`)和
`apps/argo-workflows-training-image/manifests/workflow-template.yaml`
(集群内 DNS)**复用同一份文件**,零代码重复,已经端到端跑通并在
MLflow Model Registry 里验证过结果。本 ADR 只是把这个已验证的做法从
一个脚本推广成一套约定。

### K8s 世界里唯一正确的映射:conda env → 容器镜像

algo 那套的地基是"大家共用服务器上同一批 conda 环境"。在 K8s 上,这件事
的等价物是"大家共用同一批容器镜像"。**不是 PVC 里放一个 conda,不是
运行时 pip install**(后者是本项目 `docs/BACKLOG.md` P2.1 点名的反模式,
2026-08-19 当天还因此修了 Superset 一次)。

其余所有设计都是这条的推论。

### 交付物一:`platform_sdk`——薄封装,目标 500 行以内

一个 pip 包,内容只有两类,不做 ORM、不做 DSL、不做抽象层:

1. **连接封装** (`connect.py`):`trino()` / `spark()` / `minio()` /
   `feast()` / `mlflow_setup()`,地址和凭据一律从环境变量读,调用方
   不感知自己跑在哪。
2. **提交封装** (`submit.py`):`submit_job(...)` 生成并提交 Argo
   Workflow / CronWorkflow。等价于 algo 里 `$EXECUTENOTEBOOK` +
   `pydolphinscheduler` 那层。

用户 notebook 第一行 `from platform_sdk import trino` 就补掉了
usage-guide 记录的那个缺口——**那个"产品差距"的解法是一个函数,不是
一个平台**。

对应 algo 的 `utils/common_utils`(那里有 trino/doris/hbase/hdfs/
clickhouse/es 等一批连接器 + `execute_notebook` + `dolphinscheduler_api`),
形状一致,只是后端换成这个平台的组件。

### 交付物二:项目脚手架——一条命令生成骨架

不引入 cookiecutter(多一个依赖),就是一个脚本复制模板目录:

```
jobs/<项目名>/
├── job.py       # 业务代码,import platform_sdk
├── job.yaml     # 镜像 / 资源 / 调度 / 参数,10 行以内
└── README.md
```

用户要学的新东西只有 `job.yaml`,不需要懂 Argo Workflow YAML、
Airflow DAG、Kubernetes。

### 交付物三:统一镜像——环境一致的物理保证

一个 Dockerfile,装 `platform_sdk` + 常用依赖(trino client / pyspark /
mlflow / feast / pandas / scikit-learn)。**JupyterHub 的 singleuser pod
和调度器的任务 pod 用同一个镜像。**

这直接回答"Airflow 里也能用等同的环境吗":不是"等同",是同一个。

本仓库已有三个自建镜像的先例(`apps/feast/feature-server-image/`、
`apps/argo-workflows-training-image/`、`apps/superset-image/`),包括
已经踩平的 PyPI 限速(阿里云 mirror)和离线缓存(`image-cache-amd64/`)
两个坑,不是从零开始。

多环境(类似 algo 的 21 个 conda env)用 JupyterHub chart 原生的
`singleuser.profileList` 暴露成登录时的下拉选择——**chart 自带能力,
零二开**,等价于 GCP 的 runtime template。已确认 chart 4.4.1 支持
(`helm show values` 第 443 行 `profileList: []`)。

### 交付物四:Skills——让用户的 AI 成为使用界面

放在仓库 `.claude/skills/`,跟着 git 走:

- `submit-job`:怎么新建项目、提交、查状态、看日志
- `query-data`:怎么查表、查自己有什么权限、找数据
- `debug-job`:任务失败了怎么排查

**传统平台把这一层做成 web UI + 几十页文档;这里做成 skill。**
新同事 clone 仓库,他的 Cursor/Claude 就会用这个平台。维护成本比 UI 低
一个数量级,而且天然贴合 zhenghe "单人公司、核心运维靠 AI"那个设想
(见 ADR-048 AI 运维角色)。

这也是 zhenghe 一直要的 "cookbook" 的可执行版本——文档会过期没人发现,
skill 被 AI 实际执行,错了当场就暴露。

### 明确不做(这是本 ADR 最重要的部分)

以下每一项都是"看起来像平台该有的",但会同时违反三条约束里的至少两条:

- **web IDE / 在线代码编辑器**——直接抵消 zhenghe 明确要保住的
  "本地 AI 编程工具能力",是负价值,不是优先级低。
- **拖拽式 DAG 设计器**——二开成本极高,升级最痛,而且 workflow-as-code
  在 algo 那边已经验证是更好用的形态。
- **自建元数据服务**——OpenMetadata 已部署。
- **自建权限系统**——Keycloak + OPA 已部署(ADR-051)。
- **自建调度 UI**——Airflow / Argo Workflows 自带。

### 工作组共享目录:有设计,但先不做

zhenghe 提到希望有"部门组默认共享的目录 + 自己的文件夹 + 能分享给别人"。

技术方案是清楚的:JupyterHub chart 原生 `singleuser.storage.extraVolumes`
(零代码)挂一个 RWX 卷到 `/home/jovyan/teams/<组名>`。但 k3s 默认的
`local-path` StorageClass **只支持 RWO**(已实测确认当前每个用户的
`claim-<user>` 都是 RWO),要做共享目录必须先装 NFS provisioner——
这是整套方案里**唯一的真实基础设施增量**。

**判断:先不做。** 代码共享走 git(和 algo 现在的做法一致,也是唯一
跟 AI 编程工具兼容的方式),数据共享走湖(Iceberg/MinIO,已有),两者
覆盖绝大部分场景。等真的有第二个人在用、出现具体的共享诉求时再加,
那时是半天的工作量,不会因为现在没做而变难。

**同时要立一条规矩:代码共享永远走 git,不走共享目录。** 否则工作组
目录会退化成"几个人在同一个文件夹里互相覆盖",这是比没有共享目录更差
的状态。

## 后果

### 好的方面

- 自研代码总量预计 **1000 行以内**(SDK ~500 + 脚手架 ~100 + skill 若干
  markdown),对比 DataLeap 那类产品是几十万行量级。
- SDK 只依赖各组件**稳定的客户端协议**(Trino DBAPI、S3、MLflow REST),
  组件版本升级基本不影响它——这是"避免不好维护和升级"这条约束的
  直接落实。
- skill 和脚手架是纯文本,零升级成本;没有前端,没有 npm 依赖树,
  不用追前端 CVE。
- 保住了本地 IDE + AI 编程工具的完整能力,平台不插在人和环境中间。

### 代价和风险

- **`platform_sdk` 会成为一个新的、必须自己维护的东西**。控制手段是
  硬性约束它的职责边界:只做"连接"和"提交"两件事,任何"顺手加个功能"
  的想法默认拒绝,记进 backlog 单独评估。这条边界一旦破了,它就会长成
  一个小型平台,本 ADR 的全部价值就没了。
- **镜像变成新的耦合点**:所有人共用镜像,意味着加一个依赖要重新构建、
  重新分发。这是 conda 共享环境本来就有的代价(algo 那边也一样),不是
  这个方案引入的新问题,但要在 skill 里写清楚"怎么申请加依赖"。
- **skill 这层是新东西,没有先例可循**。本项目此前没有 `.claude/skills/`
  目录,效果好不好要真实用过才知道,不能假设一定成立。第一批只做
  `submit-job` 一个,验证有效再扩。

### 实施顺序(每一批都要能独立验证)

1. **统一镜像 + `platform_sdk` 的连接封装**——先解决 usage-guide 里
   记录的那个"没有自动连 Trino"的缺口,这是当前体验差距最大的一块,
   而且不依赖其他任何一批。
2. **`submit_job` + 项目脚手架**——把今晚跑通的 Argo 训练链路收敛成
   用户能自助使用的形态。
3. **`submit-job` skill**——前两批有东西可用之后才有意义。
4. (可选,有第二个用户时再做)工作组共享目录 + NFS provisioner。

## 相关

- [ADR-025](025-jupyterhub-sso.md) JupyterHub SSO(本 ADR 在其之上加
  开发体验层)
- [ADR-048](048-ai-operator-role.md) AI 运维角色(skill 这层和它同源)
- [ADR-057](057-architecture-review-2026-08-19.md) 架构盘点,其中
  "notebook 里触发训练"这条空白由本 ADR 的第 2、3 批覆盖
- `docs/usage-guide.md` "交互式开发 / Notebook" 一节记录的真实差距
- `docs/BACKLOG.md` P2.1 "停止运行时 pip install"——本 ADR 的统一镜像
  是它在开发者体验这条线上的落实
