# 054. cloud-full 裸机(VM)引导流程:不止是给阿里云用的,以后自建 IDC 也是同一套

- 状态: 进行中,持续更新——这份 ADR 在真实搭建 cloud-full 环境的过程中
  同步写,不是搭完之后回忆着补,记录的是实际踩到的坑和当场做的决定。

## 背景

用户明确要求:这次搭 cloud-full 环境的过程要记录清楚,因为**以后大概率
要迁移到自建 IDC**,不希望到时候又要重新踩一遍同样的坑、白忙活一次。

这份 ADR 的定位:`environments/cloud-full/README.md` 讲的是"接入
cloud-full 硬件之后,每个组件该给多少资源"(应用层),这份讲的是**更底层
一步**——"一台全新的裸机(不管是云厂商 VM 还是自建 IDC 的物理机/虚拟机),
怎么从零变成一个能跑 k3s 的环境"。`scripts/21-bootstrap-cloud-vm.sh`
是这个流程的脚本化产物。

## 关键判断:这次踩的"连不上境外站点"的坑,不是阿里云特有的,自建 IDC 大概率也会遇到

这是这份 ADR 最重要的一条判断,直接决定"现在做的事对以后有没有用":

用户当初要求提前打包离线镜像("到时候上云可能无法访问中国以外的网站"),
这个顾虑的根源是**国内网络环境**,不是"阿里云这一家云厂商的限制"——自建
IDC 大概率是在国内机房,同样的出口限制大概率也会遇到。所以这次为了绕开
`get.docker.com`/GitHub releases 连不上而摸索出来的解法(国内镜像源),
不是"阿里云专属的临时对付办法",是**以后大概率还要用一次的通用经验**,
这也是为什么要花时间正经记下来,不是图省事直接手动敲命令了事。

## 实测踩到的坑和解法(2026-08-15,真实操作记录)

### 1. 数据盘设备名不能硬编码

云主机的数据盘设备名(`/dev/nvme0n1` vs `/dev/vdb` 之类)因云厂商/机型
而异,自建 IDC 用的虚拟化方案(比如 KVM/libvirt)命名规则又可能不一样。
`scripts/21-bootstrap-cloud-vm.sh` 里找数据盘的逻辑是"遍历所有
`nvme*` 设备,找没有挂载点、没有文件系统的那一块",不是写死某个具体
设备名——但这个逻辑本身还是**限定了 `nvme*` 这个前缀**,自建 IDC 如果
用的是 `/dev/vd*` 或者 `/dev/sd*` 这类设备名,这段逻辑需要改,不能直接
照搬。这是已知的、留给以后接自建 IDC 时要调整的地方,不是这次没考虑到。

### 2. Docker 官方安装脚本(`get.docker.com`)连不上,换阿里云 apt 镜像源

实测:`curl https://get.docker.com` 直接 `Recv failure: Connection
reset by peer`。改用 Docker 官方 apt 仓库的**阿里云镜像**
(`https://mirrors.aliyun.com/docker-ce/linux/ubuntu`)——这是阿里云
自己维护的官方软件镜像站,不是来源不明的第三方,符合"只用官方支持方式"
的门槛(内容还是 Docker 官方发布的包,只是换了一个下载入口)。具体是
标准的"加 GPG key + 加 apt source + apt-get install docker-ce"三步,
和 Docker 官方文档记录的 apt 安装方式完全一样,只是把 `download.docker.
com` 换成了 `mirrors.aliyun.com/docker-ce`。

**这条以后接自建 IDC 时怎么复用**:如果 IDC 机房同样出不了国外网,先确认
IDC 网络能不能连 `mirrors.aliyun.com`(大概率能,这是国内主流镜像站,
很多公司内网都会走类似的镜像);连不上的话,大概率公司自己有内部的
apt/yum 镜像源(很多国内公司 IDC 都有自建镜像站这个惯例),把
`docker.list` 里的地址换成公司自己的镜像地址,流程不用变。

### 3. k3s 官方安装脚本能连上,但脚本内部下载的二进制文件(GitHub Releases)连不上

这是这次最容易被忽略的一个坑:`curl https://get.k3s.io` 本身返回
`200 OK`(脚本文件小,托管方式和实际二进制下载是分开的),看起来"连得上",
但脚本内部实际下载 k3s 二进制走的是
`github.com/k3s-io/k3s/releases/download/...`,这条路径卡死不动(挂了
好几分钟没有任何进展,用 `ps aux` 确认那个 `curl` 进程一直挂着)。**这种
"入口脚本能连,但脚本内部真正要下载的东西连不上"是一类值得警惕的坑**——
不能因为最外层的 curl 返回 200 就断定这条路径整体可用,大文件/关键
二进制往往走的是另一条完全不同的 CDN 路径。

解法:改用 k3s 官方文档记录的**中国大陆镜像方式**(`INSTALL_K3S_MIRROR=
cn`,把脚本换成 `rancher-mirror.rancher.cn/k3s/k3s-install.sh`)——这
同样是 k3s/Rancher 官方自己提供、文档里明确写出来的选项,不是三方野路子。

**以后接自建 IDC 时怎么复用**:同上,先测 IDC 网络能不能连
`rancher-mirror.rancher.cn`;连不上的话,retry 时至少已经知道"要去找
k3s 二进制的国内镜像/内部镜像",不用从头排查"为什么装了一半卡住不动"
这个过程本身。

### 4. k3s 的 `--data-dir` 没有 100% 生效,有一小部分状态仍然写进默认路径

`--data-dir /data/k3s` 确认了主要数据(etcd/容器运行时状态,几百 MB
量级)确实写进了指定目录,但 `/var/lib/rancher/k3s` 下仍然会有一份
独立的、真实存在(不是软链接)的目录,持续占用几百 MB 级别的空间——
`readlink -f /var/lib/rancher/k3s` 确认不是软链接,是真目录,说明某些
k3s 子系统(具体是哪个还没深挖)不完全遵守这个参数。**这次没有深究根因,
判断是可以接受的**:系统盘(40GB)还有 34GB 空闲,几百 MB 级别的残留
不构成真实风险,值得记录但不值得为了这个继续排查下去。以后如果这部分
残留持续增长(不是稳定在几百 MB),需要重新评估。

### 5. containerd 自己的存储不跟着 Docker 的 `data-root` 走——这个坑比第 4 条严重得多,真的把系统盘写满过

第 4 条记的是"k3s 一小部分状态没跟着 `--data-dir` 走,几百 MB 级别,
判断可以接受"——这条是**同一类问题的严重版本,不能同样"可以接受"**,
如实记录更正。

`/etc/docker/daemon.json` 里配的 `"data-root": "/data/docker"`,只管
Docker 经典的 overlay2 graph driver 那部分。现代 Docker 默认启用
**containerd 镜像存储后端**(这也是这个项目更早之前在本机 Mac 上,
`docker save` 需要显式加 `--platform` 参数那次坑的同一个根因,见
`scripts/export-image-cache-amd64.sh` 的注释),这部分内容走的是
containerd 自己独立的 `root`/`state` 配置(`/etc/containerd/
config.toml`),**默认值是 `/var/lib/containerd`,完全不受 `data-root`
影响**。

实测后果:批量 `docker load` 灌镜像时,系统盘(40GB)被写满
(`/var/lib/containerd` 占了 35GB,`/data/docker` 只有几百 KB),后续
所有 `docker load`/`docker pull` 直接报 `no space left on device` 失败。
这不是"残留一点无关紧要的状态"那种量级,是能让整台机器的容器运行时
彻底不可用的真实故障。

修复:显式编辑 `/etc/containerd/config.toml`,把 `root` 指到
`/data/containerd`,`systemctl restart containerd && systemctl restart
docker` 生效。已经写回 `scripts/21-bootstrap-cloud-vm.sh`(Docker 安装
那一步的一部分,不是单独的步骤,因为这两个配置本来就该一起做才完整)。

**给以后接自建 IDC 的教训**:凡是"给 Docker 配置存储路径"这件事,不能
只改 `daemon.json` 的 `data-root` 就假设完事了——现代 Docker 版本(带
containerd 镜像存储)必须**同时**检查并配置 containerd 自己的
`root`/`state`,两个配置项都要指向大盘,缺一个都会在数据量上来之后
暴露问题。部署完之后应该主动跑一次 `docker pull` 一个真实镜像,确认
体积增长确实反映在预期的大盘路径上,不能只看 `docker info` 里
`Docker Root Dir` 那一行就认为配置完整生效了。

## 涉及的文件

- 新增 `scripts/21-bootstrap-cloud-vm.sh`——幂等,从 Mac 通过 SSH 远程
  执行,格式化挂载数据盘 + 装 Docker(阿里云镜像源)+ 装 k3s(Rancher
  中国镜像,`--docker` 运行时,和本机 colima 保持一致)。
- 新增 `scripts/export-image-cache-amd64.sh`(前一轮已经写了,这里
  补充记录):导出这个项目用到的镜像的 x86_64 版本,给云上/未来 IDC
  用(这个项目本机是 Apple Silicon,原有的 `image-cache/` 是 arm64,
  生产/IDC 确认是 x86_64 之后才需要这份)。

## 安全考量:K8s API server(6443)不对公网开放

云主机的安全组/防火墙没有放行 6443 端口给公网——管理这个集群走 SSH
隧道(`ssh -L 本地端口:127.0.0.1:6443 ...`),不是把 API server 直接
暴露公网。这条原则不因为环境是"云 VM"还是"自建 IDC"而改变,自建 IDC
同样应该遵守(如果自建 IDC 本身就在内网、不出公网,这条限制自然满足,
不需要额外做什么;如果自建 IDC 的管理入口也要经过公网跳板,同样应该走
隧道/VPN,不要直接开放)。

## 后续会持续补充的内容

这份 ADR 会随着 cloud-full 环境继续搭建(ArgoCD 引导、各组件部署、
镜像缓存传输加载)持续更新,把过程中真实碰到的问题和解法记进来,不是
写完这一版就结束。
