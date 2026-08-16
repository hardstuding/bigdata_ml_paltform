# Backlog

新想法/顺手发现的问题,默认进这里,不自动打断 `docs/CURRENT_WORK.md`
里的当前主线。这份文件只做索引和优先级排序,不重复描述已经在别处写清楚
的内容——每一项都指向权威来源(ADR/architecture.md),不在这里复制一遍。

## P0(会阻断当前主线的,才有资格排这里)

当前没有。如果出现真实的数据风险/持续计费异常/安全问题,加在这里,
并在 `docs/CURRENT_WORK.md` 里注明"CURRENT 被 P0 阻断,原因是……"。

## P1(cloud-full 部署收尾之后,下一段专门时间做)

排序和理由见
[ADR-055](decisions/055-external-review-response-2026-08-15.md#后续明确排期不是无限期搁置)
"后续"一节,这里只列条目:

1. 破坏性操作防护补全(目前只有轻量版
   `scripts/confirm-destructive-kubectl.sh`,评审建议的完整统一 guard
   框架还没做)
2. 三个自建 Flask 工具补测试(依赖锁定已经在任务 #13 做完,见上面
   P1.6"Python 依赖锁定"那条;**单一源码问题 2026-08-16 已解决**——
   新增 `scripts/sync-app-configmaps.py`,`src/app.py` 是唯一源码真相,
   ConfigMap 里的 app.py 从它自动生成,`--check` 模式可以接进 CI 防止
   漂移,已经实测验证过能正确检测+修复漂移。3 个 app-configmap.yaml
   顶部注释也改成指向这个脚本。**补测试进度**:`platform-portal`(11 个测试)和
   `table-registration-app`(29 个测试,覆盖 parse_columns/
   parse_table_fqn 校验逻辑——含 SQL 注入类输入、/submit 路由 Trino/
   OpenMetadata 失败/跳过/成功四种状态组合)都已经做完,全部通过。
   过程中顺带发现本机没装 `trino` 这个包,用之前任务#13 锁定的版本
   (`trino==0.338.0`)装上了。`permission-request-app`(1388行,三个里最大最复杂)这次也补了测试,
   但**如实说明覆盖范围不是全部**:测的是 `build_approval_steps`/
   `get_manager_chain`/`load_employees`/`is_approver` 这一组"按 ADR-040
   规则算出谁要审批"的核心逻辑(20 个测试全过,专门覆盖了 app.py 注释里
   提到的那个真实修过的"L2/L3 重复审批人"bug,作为回归测试锁住),
   `/request`/`/table-access/*` 这些路由的完整状态机(approve/reject/
   escalation/transfer/audit/external-callback)**还没测**,涉及的
   分支/外部依赖(git clone+push、企微 webhook、外部 OA 回调)比另外
   两个 app 多得多,量级上应该算独立的后续任务,不是这次顺手能做完的。
   3 个 app 现在都有了测试起点,不再是从 0 开始)
3. 环境 overlay 重构(local-lite/cloud-full/prod 真正做到改配置切换,
   不是手动 `git mv`)。**2026-08-16 cloud-full 首次全套拉起时,这个
   已知差距的代价第一次真实体现**:好几个组件里硬编码了 colima 宿主机
   专用代理地址(192.168.5.2:1087),cloud-full 连不上,导致
   argo-workflows CRD 安装 Job、3 个 Flask 工具的 pip/apt 全部反复超时
   崩溃——这次先用运行时自适应探测(连得上才用代理)这种不需要重构就能
   两边工作的办法过渡(见 `docs/CURRENT_WORK.md` 对应记录里的具体
   commit),没有动这个重构本身,但再出现同类硬编码,应该认真考虑是不是
   该启动这一项了,不能一直靠打补丁应付。
4. 扩大 CI(见 ADR-055 引用的原评审 P1-3 完整清单)——**2026-08-16 迈出
   第一步**:`.github/workflows/validate.yml` 新增 `test-flask-apps`
   job,把 3 个自建 Flask 工具刚补的测试(60 个,见上面"三个自建 Flask
   工具补测试"那条)接进 CI,外加 `sync-app-configmaps.py --check` 防止
   ConfigMap 和 src/app.py 漂移。本地模拟过 CI 里的每一步命令,全部
   通过。原评审 P1-3 清单里其它更大的 CI 扩展(比如镜像构建/集成测试)
   还没做,这只是"测试写了就要接进 CI 让它自动跑"这一小步。
5. Trino OPA 真正切换生效(需要用户在场,不是延后到"不重要",是延后到
   "需要人决策的窗口",见 ADR-051)
6. iam-sync/opa-grants-sync 这两个 CronJob 在 cloud-full 上暂时被
   suspend 了(2026-08-16)——问题比其他组件更深:一个 fetch-kubectl
   initContainer 靠 apt-get+curl 到 dl.k8s.io 现装 kubectl 二进制,主
   容器又要 git clone github.com(配的还是 colima 专用代理地址)。三层
   Mac-only 网络依赖叠在一起,不是改一两行能应付的,需要专门花时间要么
   把 kubectl 换成官方 `registry.k8s.io/kubectl` 镜像(不用现装,和
   argo-workflows CRD 那次 vendor 的思路一致)、要么给 git clone 这步找
   一个 cloud-full 也能用的路径。suspend 之前先确认过 cloud-full 上
   IAM/OPA 权限同步这两件事本来就不是这次主线验收范围。
7. ~~镜像缓存 digest 校验~~——**2026-08-16 已经做出来了**,不再是延后项:
   `scripts/verify-image-digests.sh`,原因是这次 cloud-full 部署过程中
   真实靠这套核实逻辑抓到过 9 个国内镜像站(daocloud)内容和官方不一致
   的镜像(详见 `docs/CURRENT_WORK.md` 对应记录),不是纸面价值。**已知
   限制**:一次性对 60+ 个镜像跑会撞上 Docker Hub 匿名拉取限流,出现过
   同一个镜像两次查到不同"官方 digest"的自相矛盾结果,脚本注释里已经
   记录清楚,现在更适合"抽查重点镜像"而不是"无脑跑全量清单当结论"。

## P1.5(确认必要,但还没设计——不是"按需可选")

- **Flink**:2026-08-15 用户明确说"作为新的大数据平台有它的必要性"——
  从 `docs/architecture.md` 路线图里"Phase 4 按需"这种偏可选的定位,
  改成"确认要做,还没排期设计"。目前项目里唯一沾边的是
  [ADR-011](decisions/011-seatunnel-not-airbyte.md)提到 SeaTunnel
  "支持跑在 Flink 上做真正的低延迟流式同步",但实际部署用的是 SeaTunnel
  自带的 Zeta 引擎,没有真的接 Flink。**2026-08-16 设计已经做完**,见
  [ADR-056](decisions/056-flink-role-design.md):结论是 Flink 应该定位
  成"流式计算引擎"(实时聚合/join/特征计算),不做"数据搬运"(那是
  SeaTunnel + 未来的 Kafka Connect+Iceberg Sink Connector 该干的事);
  引入顺序上,Kafka 现在零真实消费者、Kafka Connect+Iceberg Sink
  Connector 这条 ADR-011 设计好的轻量路径也从没搭过,这两步应该先于
  Flink。**这份 ADR 只是设计,没有部署任何东西**,不在当前 CURRENT
  (cloud-full 部署上线)范围内实现。

- **Spark 4.x 评估**:2026-08-15 外部(Codex)review 指出仓库固定用
  Spark 3.5.9(SparkApplication/History Server/Feast 自建镜像里的
  PySpark 都是这个版本,`iceberg-spark-runtime-3.5_2.12:1.10.0`),而
  Spark 官方发布线已经到 4.x(4.0.4/4.1.3/4.2.0,已核实 `spark.apache.
  org/downloads.html` 属实)。核实过 Codex 提出的具体理由后,结论是
  "方向对,但支撑这个方向的一条关键理由是错的":
  - 属实:Spark 4.x 默认 Scala 2.13(2.12 被移除)、Java 17 起步、
    `iceberg-spark-runtime-4.0_2.13`/`4.1_2.13` 确实已经在 Maven
    Central 发布(1.10.0)。3.5.9 本身不是废弃版本(2026-07 的维护版
    补丁),但确实是旧的大版本线,值得评估升级。
  - **不属实**(Codex 原话暗示"升级 Spark 4 可能就不用锁 Hive 3.1.3
    了"):专门查证过,Spark 4.0 的 SPARK-45265"Support Hive 4.0
    metastore"是 Spark 自己内置 Hive 客户端(HiveExternalCatalog)的
    改动,不是 Iceberg 自己的 Hive Catalog 客户端。真实证据
    apache/iceberg#13572:有人在 Spark 4.0.0 +
    `iceberg-spark-runtime-4.0` 上连 Hive 4.0.1 metastore,报的还是
    同一个 `Invalid method name: 'get_table'`——升级 Spark 大版本本身
    不能解开 Hive 3.1.3 这个限制,已经把这条更正记进
    `apps/hive-metastore/manifests/deployment.yaml` 的注释里,避免
    以后重新踩这个误判。
  - 涉及的联动变化(Java 11→17、Scala 2.12→2.13、Iceberg runtime、
    Hadoop/S3A 依赖、PySpark/Feast/History Server/Airflow 作业版本对齐、
    ANSI SQL 默认开启的行为差异)确认属实,升级不是改个版本号那么简单,
    值得做成独立的、不影响现有 3.5.9 基线的 PoC(Spark 4.1.3 + Java 17 +
    Scala 2.13 + Iceberg 1.11.0,不是抢先上 4.2.0——**已核实**
    `iceberg-spark-runtime-4.2_2.13` 在 Maven Central 上不存在,不是"还没
    找到证据",是真的没发布,4.2.0 现在没法用来跑 Iceberg,不是保守选择,
    是硬约束)。不在当前 CURRENT(cloud-full 部署上线)范围内做,cloud-full
    收尾之后再排期,local-lite 继续用 3.5.9 当已验证基线。
  - **2026-08-15 本地已经做过一轮轻量 PoC 验证**(纯 `docker run`,没碰
    colima 里的 k3s/MinIO/Hive Metastore,验证完已清理干净):
    `apache/spark:4.1.3-python3`(Scala 2.13.17 / Java 21 Temurin,这台
    Mac arm64 原生跑)+ `iceberg-spark-runtime-4.1_2.13:1.11.0` 建表/
    INSERT/MERGE INTO/snapshots 元数据表查询全部成功;额外验证了 S3A 链路
    (真实起了一个临时 MinIO 容器,不是纸面推演):Spark 4.1.3 镜像自带
    `hadoop-client-api/runtime` 是 **3.4.2**(不是项目现在 3.5.9 用的
    3.3.4),对应 `hadoop-aws:3.4.2` 的依赖是 `software.amazon.awssdk:bundle`
    (AWS SDK **v2**,不再是 3.3.4 那条线用的 SDK v1)——这意味着升级到
    Spark 4 之后,Spark 这边的 S3 SDK 会和 Trino(已经是 SDK v2)对齐,
    消除现在两边 SDK 版本不一致的情况。用这组版本(`hadoop-aws:3.4.2`)对
    真实 MinIO 做了读/写/追加写验证,全部成功。**特意没有用
    `hadoop-aws:3.5.0`**(Maven Central 上真实最新版)——因为它要匹配的是
    镜像自带的 hadoop-client 版本(3.4.2),不是"哪个最新用哪个",装
    3.5.0 大概率复现这个项目已经真实踩过一次的
    `ClassNotFoundException`(class file 版本不匹配)那类坑,同样的道理
    也适用于以后任何"依赖库版本要不要跟着升到最新"的判断。
    完整 PoC(History Server、Feast、Airflow 作业、Hive Metastore 4.2 并行
    验证等)仍然排在 cloud-full 收尾之后,这次只是先确认基础可行性。

## P1.6(2026-08-15 外部 Codex 审计——已逐条核实,PostgreSQL/Kafka/Trino/Redis/KServe runtime 部分)

原始建议保留在 `docs/claude-improvement-recommendations-2026-08-15.md`。
**2026-08-15 已经逐条上官方发布页/Maven Central/GitHub 核实**(不是照抄
Codex 的表格),结论:Codex 给的具体版本号和技术判断**基本全部属实**,
没有编造或过时到失真的地方,唯一比 Codex 原话更严重的是 Redis 这条。

- **PostgreSQL**:16.6 → 官方最新 minor 是 **16.15**(Codex 说 16.14,
  方向对、数字已经又推进了一个补丁版,来源:postgresql.org/support/
  versioning)。CloudNativePG operator 对新版本的支持面这次没查,升级前
  要先确认。
- **Kafka**:4.3.0 → **4.3.1 真实存在**,官方发布公告确认修了约 15 个
  问题,核心是 Kafka Streams RocksDB 原生内存泄漏,对应真实 JIRA
  **KAFKA-20616**/**KAFKA-20688**(来源:kafka.apache.org 官方 4.3.1
  发布公告)。Codex 这条完全准确。
- **Trino**:480 → **483**(来源:trino.io/download.html)。Codex 准确。
- **Redis**:项目里 `redis:7-alpine` 是浮动 tag,镜像缓存里另外出现过
  `redis:8.6.4-alpine`。核实到的官方最新稳定版是 **8.4.5**
  (2026-07-23,来源:redis.io 官方 8.4 分支 RELEASENOTES)——**这是一次
  安全发布**,修了一个 `RESTORE` 命令恢复 stream 消费组时的
  use-after-free,**可导致远程代码执行(RCE)**,还有 RedisBloom/TDigest
  的越界写。**比 Codex 原话("先锁定具体补丁版本")更紧急**,这是真实的
  安全修复,不只是"该锁版本了"。

  **`8.6.4-alpine` 那个疑点已经查清楚,而且发现了一个比 RCE 更值得优先
  处理的问题**:那个版本号根本不是这个项目自己引用的——顺着
  `scripts/list-project-images.py` 的扫描逻辑逐个 chart 排查,来源是
  **ArgoCD 自己的 Helm chart(`argo-cd`)自带的内置 Redis 依赖**(ArgoCD
  用它做缓存),和 Feast 那个 `redis:7-alpine` 是完全不同的两个 Redis
  实例,不存在版本号"对不上"这回事。但顺着这条查下去,在 argo-cd chart
  的 `values.yaml` 里发现一条官方注释:`# Do not use 7.4.0 <= v < 8.0.0,
  otherwise you are no longer using an open source version of Redis`
  ——Redis 在这个版本区间被 Redis Ltd 换成了限制性更强的许可证
  (RSALv2/SSPLv1),8.0 之后才又变回开源(AGPLv3,"Redis Open
  Source")。**实测确认这个项目 Feast 用的 `redis:7-alpine`(浮动 tag)
  现在解析到的真实版本是 `7.4.10`**(`docker run --rm redis:7-alpine
  redis-server --version` 实测),**正好落在这个非开源许可证区间里**——
  不只是"版本旧该打安全补丁",是现在跑着的 Redis 镜像本身可能已经不是
  开源许可证了,优先级应该在 RCE 修复之上。好消息是解法是同一个动作:
  直接升到 8.x(核实过的 8.4.5)既修了 RCE,也回到了开源许可证,不需要
  分两步。
- **KServe ServingRuntime(TF Serving 2.6.2/Triton 23.05/TorchServe
  0.9.0)**:**精确核实**——这三个版本号就是 KServe v0.19.0 官方
  `config/runtimes/kustomization.yaml` 自己写死的(来源:
  raw.githubusercontent.com/kserve/kserve/v0.19.0/config/runtimes/
  kustomization.yaml),不是这个项目装的时候选旧了。同一份文件里还发现
  `sklearnserver`/`xgbserver`/`pmmlserver`/`paddleserver`/`lgbserver`/
  `predictiveserver`/`huggingfaceserver`(+`-gpu`)这 8 个 runtime 官方
  自己用的就是字面 `latest`/`latest-gpu`——Codex"官方默认里混着
  latest"这条完全准确,而且更精确的做法是:各个单独 runtime YAML 用的
  是 kustomize 占位符 `:replace`,真正版本号是在 `kustomization.yaml`
  的 `images` 转换表里替换进去的,升级思路应该是**整体替换这份
  images 转换表**,不是逐个改单独 YAML。
- **暂时保留,不算落后**(沿用 Codex 原判断,这次没有单独复核):
  SeaTunnel/MLflow/OpenMetadata/OPA/KServe 控制面/Spark Operator/
  ArgoCD/Argo Workflows/Grafana 系/Keycloak/cert-manager,Airflow 3.2.2
  (3.3.0 是功能性升级,不是安全修复,不紧急)。
- **这几条已经全部处理完,不再是"还没核实"**:Triton 23.05→**26.07-py3**
  (2026-08-16 核实并升级,`apps/kserve-runtimes/manifests/
  kustomization.yaml`,匿名拉取不需要 NGC 账号/API key,`-py3` 是通用
  tag、CPU 也能跑);KServe 官方 `images` 转换表(7 个 latest 全部固定
  版本+digest)已经在任务 #13 做完,见上面 KServe ServingRuntime 那条;
  Python 依赖锁定(Flask/dbt 等 5 处)也已经在任务 #13 做完,见上面
  Python 依赖锁定那条。TF Serving(2.6.2)/TorchServe(0.9.0)/mlserver
  runtime 矩阵设计(要不要精简/换掉某些推理框架)仍然是故意没做的更大
  设计判断,留在 P1。

**2026-08-15 已执行的低风险升级(任务 #13)**:
- PostgreSQL 16.6→16.15:8 处引用(`apps/{openmetadata,keycloak-db-init,
  postgres-backup,mlflow,hive-metastore,superset,airflow}` 的
  create-db-job/cronjob + `apps/postgres/manifests/cluster.yaml` 的
  CNPG `imageName`)全部改完,Docker Hub/ghcr.io 两个 tag 都实测确认
  存在。
- ~~Trino 480→483~~——**2026-08-16 已回退到 480**:`helm template` 渲染
  diff 只能比出"改了一行 tag",测不出运行时行为差异。真正在 cloud-full
  上拉起来才发现 483 对 `http-server.http.port` 收紧了配置校验,chart
  自己无条件生成这行属性(`configmap-coordinator.yaml` 模板里写死,不受
  `http.enabled` 控制),我们关了 `http-server.http.enabled`(只用
  OAuth2+HTTPS)之后这个"未使用的属性"在 483 上直接报错拒绝启动,480
  上不报错。已改回 chart 默认的 480,不再单独覆盖 tag。**教训**:纯
  manifest diff 比对不足以验证跨版本兼容性,以后这类覆盖要么真的拉起来
  跑一遍,要么去读官方 changelog 确认有没有校验行为变化,不能只信
  render 结果一致。
- Feast Redis:`apps/feast/manifests/redis.yaml` 从浮动的 `redis:7-alpine`
  改成固定 `redis:8.4.5-alpine`(同时解决 RCE 安全修复和许可证问题两件
  事)。
- **Kafka 4.3.0 没有升级,是刻意决定不是遗漏**:Strimzi operator 当前是
  1.1.0(也是目前最新发布版),它的 `kafka-versions.yaml` 支持列表里只有
  4.3.0,还没收录 4.3.1,升上去 Strimzi 会直接拒绝这个 CR。已经在
  `apps/kafka/manifests/kafka-cluster.yaml` 里加注释说明,等 Strimzi 出
  支持 4.3.1 的新版本再跟上。
- **KServe ServingRuntime**:7 个官方浮动 `latest`/`latest-gpu` 全部固定
  ——vendor 到新建的 `apps/kserve-runtimes/manifests/`(不再实时
  `kubectl apply -k` 官方 GitHub,那种写法不可重现),统一改成
  `v0.19.0`(和 kserve-resources 控制面版本对齐,不是抢先用刚发布的
  v0.20.0)+ Docker Hub 实测查到的 digest。`scripts/10-install-kserve-
  serving-runtimes.sh` 和 `scripts/list-project-images.py` 都同步改成读
  本地 vendor 的内容,`kubectl kustomize` 渲染验证过。tensorflow-serving/
  mlserver/tritonserver/torchserve 这几个本来就有显式版本号的没有改动
  ——是否该换掉这几个推理框架是更大的"支持哪些引擎"设计判断,不在这次
  "只固定 latest"范围内。
- **Python 依赖锁定**:5 个自建 Flask/dbt 场景(`platform-portal`/
  `permission-request-app`/`table-registration-app`/`iam-sync`/
  Airflow 里的 dbt DAG)全部从不锁版本的 `pip install flask requests`
  这类,改成显式版本号(PyPI 实测确认存在:flask==3.1.3、
  requests==2.34.2、pyyaml==6.0.3、trino==0.338.0、dbt-core==1.10.23、
  dbt-trino==1.10.3、boto3==1.43.72)。dbt-core 特意没有跟到 PyPI 最新的
  1.12.2——dbt-trino 适配器目前只发布到 1.10.3,虽然它声明的依赖范围
  技术上兼容 1.12.x,但没有证据表明真的测试过,保守选同一条 1.10.x 线。
  所有 5 处 `python:3.12-slim` 基础镜像也顺手固定了 digest(Docker Hub
  实测查到的 amd64 digest)。这轮改的是"显式固定版本"这个最小范围,
  没有触碰"要不要建 requirements.lock/uv.lock + CI 构建镜像"这个更大的
  架构改动(那是 P1 里"三个自建 Flask 工具补测试/锁依赖"的范围)。

下一次真正做这轮审计时,第一步应该是先修好 Codex 提到的"版本审计脚本自己
因为本机没装 PyYAML 直接跑不起来"这个问题(如果这个项目已经有一个自动
版本清单脚本的话,需要先找到是哪个、确认它现在能不能跑),不是凭这次的
文字建议直接改版本号。

## P2(5 条产品主线——分析师/开发/算法/运维/管理岗的完整体验)

完整方案见 `docs/architecture.md`"Phase 4 之后"一节和原始评审
`docs/claude-improvement-recommendations-2026-08-15.md`。排序:
可靠底座(即上面 P1)→ 统一项目模型 → 分析师黄金路径 → 大数据开发
黄金路径 → 算法黄金路径 → 运维控制面+管理驾驶舱 → 新引擎评估
(ClickHouse 等)。**这五条现在都还没开始**,不要误以为在做。

- **Stackable(Spark/Trino/Hive 统一 Operator 平台)——2026-08-15 Codex
  新提出,这个项目目前完全没评估过**:值得找时间单独做一次 PoC(非生产
  环境、对比现有 Helm/Operator 方案的部署/升级/故障恢复成本、通过统一
  的计算引擎适配层调用、保留绕开它直接用官方 Operator 的能力),不是
  现在就迁移任何现有组件。已知代价:版本支持明显滞后社区发布(比如
  当前 Spark 长期支持线停在 3.5.8),CRD/Operator/镜像会形成中等到较高
  程度的平台绑定。详细判断见 `docs/architecture.md`"Phase 4 之后"一节
  2026-08-15 补充部分。

## 曾经提出、明确决定不做/暂缓的

- **需求追踪矩阵**(`docs/requirements.md`,给每条用户需求分配 ID 逐条
  验收):评审建议里的一项,认可其价值,但补建这个矩阵需要回溯梳理
  ~50 篇 ADR 的历史内容,工作量本身就是一个独立任务,不现在做。如果
  以后真的再发生一次"某条需求被忘了"的事故,优先级要重新评估。
- **自建 `scripts/task-runner.sh`**(start/status/logs/stop/resume 的
  长任务管理框架):评估后判断这个项目目前是单 Claude 会话操作,Bash
  工具自带的后台任务追踪+完成通知机制已经够用,重新建一套平行机制是
  多余的封装,不做。
- **正式的多工作流并行调度表**(workstream ID/资源预算/依赖表):当前
  实际工作模式主要是单线操作,没有真的同时跑多个独立 sub-agent,先不
  建这套机制,等真的出现"经常需要精细协调 3+ 个并行工作流"的场景再说。
