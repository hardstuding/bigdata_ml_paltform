# 镜像仓库:为什么要加阿里云 ACR,以及凭据怎么配

## 问题

境内云主机拉 GHCR 的**大镜像**会直接卡死。2026-08-28 升 Spark 4 之后量到的
数字(不是印象):

| 试法 | 结果 |
|---|---|
| kubelet 拉 3.44GB 的 `spark-iceberg` | `Failed to pull image ... context canceled`(进度超时被打断)→ `ImagePullBackOff` |
| 在节点上 `docker pull` 同一个镜像 | **25 秒 0 字节**,`/data/docker` 完全不增长 |
| `ghcr.nju.edu.cn` 镜像站 | manifest 拿得到(HTTP 200),拉 blob 30 秒超时 |
| 同一时间拉 ~100MB 的 `platform-portal` | 正常 |

结论:不是 GHCR 挂了,是**大 blob 过不去**。当天只能用
`scripts/38-ship-image-to-cloud.sh` 把 1.3GB 的 tar 手工搬上去——能用,但
每次改镜像都要人在场,和"一键部署"直接冲突。

**为什么不能靠 registry mirror 解决**:这台机器的 k3s 是用 `--docker`
(cri-dockerd)起的,`/etc/rancher/k3s/registries.yaml` **根本不会被读**
(那是 k3s 内置 containerd 的功能)。cri-dockerd 下只剩 Docker daemon 的
`registry-mirrors`,而它**只能镜像 Docker Hub**,ghcr.io 不支持。详见
`docs/project/roadmap.md`「镜像拉取」那条。

## 解法

把自建镜像同步一份到**阿里云 ACR**。ACR 和 ECS 同地域走内网,不经过
公网出口。`.github/workflows/build-images.yml` 已经写好了:一次构建、
推两个 registry(不是构建两次——同一份产物推到多个 tag,两边内容和 digest
完全一致,不会出现"ACR 那份和 GHCR 那份其实不一样"这种最难查的问题)。

**没配 secrets 时这几步自动跳过**,流水线不会因此变红。

## 凭据怎么配(这一步只能仓库所有者自己做)

> **AI 协作者不经手这些值。** 不要把 ACR 密码贴进对话、issue、或任何
> 文件里——贴进去就等于泄露,即使随后删掉。下面两种办法都是你自己操作、
> 值直接进 GitHub 的加密存储,谁都读不出来(包括我)。

需要 4 个仓库级 secret:

| 名字 | 值 | 例子 |
|---|---|---|
| `ACR_REGISTRY` | ACR 实例的域名 | `registry.cn-wulanchabu.aliyuncs.com` |
| `ACR_NAMESPACE` | ACR 里的命名空间 | `bigdata-ml-platform` |
| `ACR_USERNAME` | ACR 登录账号 | 通常是阿里云账号名或子账号 |
| `ACR_PASSWORD` | **ACR 访问凭证密码**,不是阿里云主账号密码 | 在 ACR 控制台「访问凭证」里单独设 |

### 办法一:网页(推荐,不需要装东西)

1. 阿里云控制台 → 容器镜像服务 ACR → 选和 ECS **同地域**的实例
   (这台 ECS 在 `cn-wulanchabu`,选乌兰察布)
2. 「命名空间」新建一个,记下名字 → 这是 `ACR_NAMESPACE`
3. 「访问凭证」→ 设置固定密码 → 这是 `ACR_USERNAME` / `ACR_PASSWORD`
4. GitHub 仓库页 → Settings → Secrets and variables → Actions →
   New repository secret,把上面 4 个逐个加进去

### 办法二:命令行

```bash
gh secret set ACR_REGISTRY --repo hardstuding/bigdata_ml_paltform
gh secret set ACR_NAMESPACE --repo hardstuding/bigdata_ml_paltform
gh secret set ACR_USERNAME --repo hardstuding/bigdata_ml_paltform
gh secret set ACR_PASSWORD --repo hardstuding/bigdata_ml_paltform
```

每条会提示你输入值,**输入内容不会出现在 shell 历史里**。

## 配完之后:让集群从 ACR 拉

两步,**都不需要把密码写进任何文件或命令行**。

### 1. 集群侧的拉取凭据

```bash
read -s -p "ACR 密码: " ACR_PASSWORD && export ACR_PASSWORD && echo
export ACR_REGISTRY=crpi-xxxx.cn-hangzhou.personal.cr.aliyuncs.com
export ACR_USERNAME=<阿里云账号名>
export ACR_NAMESPACE=<命名空间>
./scripts/45-configure-acr-pull.sh
unset ACR_PASSWORD
```

`read -s` 不回显、也不进 shell 历史。脚本把凭据建成 `acr-pull` Secret 并挂到
各命名空间的默认 ServiceAccount 上,这样 Pod 自动带上,不用逐个 Deployment
改 `imagePullSecrets`。

**凭据只发给会拉自建镜像的那些命名空间,不是所有命名空间** —— imagePullSecret
是凭据,撒得越广泄露面越大。加新组件时那份名单要跟着加。

### 2. 把清单里的镜像地址切过去

```bash
ACR_REGISTRY=... ACR_NAMESPACE=... python3 scripts/switch-image-registry.py --to acr
python3 scripts/render-environment-config.py cloud-full
git commit -am "镜像地址切到 ACR" && git push
```

只动**自建镜像**(镜像名取自 CI workflow 的 matrix,不是手写一份 —— 手写
的下场是加了新镜像忘了同步,表现成单个组件 ImagePullBackOff)。第三方镜像
(apache/spark、trino 等)一律不碰,它们不在我们的 registry 里。

切换是可逆的:`--to ghcr` 换回去,内容完全还原(验证过)。**开源使用者和
境外环境应该留在 GHCR**,ACR 是这套部署的境内加速,不是项目的默认。

`--show` 随时看现在指向哪。

> **已经在跑的 Pod 不会自动换镜像**,要等 ArgoCD 同步后滚更。

## 已知的取舍

### 跨地域

推到 ACR 只解决了"镜像在境内有一份",**集群里的清单还是引用
`ghcr.io/...`**,不会自动改成从 ACR 拉。要让 cloud-full/prod 用 ACR、
而仓库的事实来源仍是 GHCR,需要给渲染器加一个 `{{IMAGE_REGISTRY}}` 占位符
按环境切换。

**现在还没做,因为有真实的工作量**:22 个文件引用自建镜像,其中只有 3 个
在 `templates/` 下会被渲染,其余 19 个是直接维护的源文件。要么把它们搬进
模板体系,要么让渲染器多做一遍全仓库的前缀改写。这个取舍等 ACR 真的配好、
能实测验证之后再定,不先猜。

在那之前的临时办法仍然是 `scripts/38-ship-image-to-cloud.sh`(注意它只支持
**tag 引用**,不支持 digest,原因见脚本开头)。
