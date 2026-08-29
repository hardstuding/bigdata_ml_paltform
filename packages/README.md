# 内部包

**在这里加一个目录、push,一小时内就能 `pip install` 它。**

```
packages/
  <包名>/
    pyproject.toml
    <包名>/
      __init__.py
```

平台每小时把这里的每个包构建成 wheel,发布到内部索引。notebook 和作业 pod
的 pip 已经配好了这个索引,**不用加 `--extra-index-url`**:

```python
!pip install my-team-utils
```

## 加第一个包

```bash
mkdir -p packages/my-team-utils/my_team_utils
cat > packages/my-team-utils/pyproject.toml <<'TOML'
[project]
name = "my-team-utils"
version = "0.1.0"
description = "我们组的公共函数"
requires-python = ">=3.10"
TOML
echo 'def hello(): return "hi"' > packages/my-team-utils/my_team_utils/__init__.py
git add -A && git commit -m "加 my-team-utils" && git push
```

## 几件必须知道的事

- **发布不是立即的**,最长等一个发布周期(每小时一轮)。急的话让平台组手动
  触发一次:`kubectl -n data create job --from=cronjob/internal-packages-publish now`
- **改了代码要升版本号**。`pyproject.toml` 里的 `version` 不变的话,已经装过
  这个版本的环境不会重新下载 —— 这是 pip 的行为,不是平台的问题。
- **版本号只能往上加,不要复用**。同一个版本号发两份不同内容,是所有包管理
  问题里最难查的一类。
- **所有集群内的工作负载都能装这些包**,不按组隔离(取舍见
  [ADR-083](../docs/decisions/083-internal-package-registry.md))。**不要往内部包
  里放密钥**。
- 包名用连字符(`my-team-utils`),import 名用下划线(`my_team_utils`),
  这是 Python 的惯例,不是这个平台的规定。

## 为什么发布要走 git,而不是 `twine upload`

上传 API 意味着"这个包是哪来的"多了一条不经过 review 的旁路。走 git 的话
每次变更都有 commit、有 diff、可回溯。详见 ADR-083。

## Java / Maven 呢

同一套机制能做(Maven 仓库也只是目录布局),但**还没做** —— 等有人真的要发
第一个内部 jar 时再做,不预先造一个没人用的东西。
