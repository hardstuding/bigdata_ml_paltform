# ADR-077:Superset 汉化 —— 不只是一个配置

日期:2026-08-26
状态:**已构建并实机验证**(2026-08-28):language pack 加载成功,4054 条译文

## 起因

zhenghe 2026-08-26:"Superset 汉化,不是一个配置的事吗?"

**一半对。** 配置确实只有两个键,而且 Superset 自带 22 种语言的翻译源文件,
不用自己翻译也不用打补丁。但只加配置**界面不会变中文**。

## 第一半:两个配置键(已加)

```python
BABEL_DEFAULT_LOCALE = "zh"
LANGUAGES = {"zh": {...}, "en": {...}}
```

两个都要设:前者决定**默认**语言,后者决定语言切换菜单里**列出哪些**。
只设第一个,默认变中文了但想切回英文找不到入口;只设第二个,菜单里有中文
但默认还是英文,每个人都得自己切一次。

**保留英文不是可有可无**:报错信息、官方文档、社区搜到的答案全是英文的,
排障时切回英文才对得上号。

## 第二半:翻译根本没编译(实测发现)

配置进去之后实机验证,`kubectl exec` 进 pod `import superset_config` 能读到
`BABEL_DEFAULT_LOCALE = zh`——**配置确实生效了,但登录页一个中文都没有**。

查下来原因很直白:

```
superset/translations/ 下:22 个 .po,0 个 .mo,0 个 .json
```

`.po` 是翻译**源文件**,gettext 运行时只认编译后的 `.mo`。没有 .mo,它找不到
任何翻译目录,**静默回落到英文**——不报错,配置也确实"生效"了,就是没翻译。
这是这个项目反复出现的那类失败:**每一层都显示成功,效果没有生效**。

修法:`apps/superset-image/Dockerfile` 里加一行
`pybabel compile -d /app/superset/translations`,并且 `test` 编译出的 .mo
个数大于 0(不然哪天上游改了目录结构,又会静默回到没翻译的状态)。
这一步不改任何翻译内容、不联网,只是把已有的 .po 编译一遍。

**能这么修是因为 Superset 镜像是我们自己构建的**
(`ghcr.io/hardstuding/bigdata_ml_paltform/superset`),不是官方镜像。

## 第三半:React 主界面读的是另一套翻译(2026-08-27 实测确认)

上一节那个"可能还需要另一份产物"的猜测,实测**成立**。镜像重建后进 pod 看:

```
编译出的 .mo 个数: 22          ← pybabel compile 生效了
前端翻译 json:  只有 empty_language_pack.json
最大的 JS chunk 里的中文:  0 个
```

也就是说 Superset 的界面分两半,用的是**完全不同的两套翻译产物**:

| 界面 | 读什么 | 谁产出 |
|---|---|---|
| Flask / FAB 渲染的(登录、管理页) | gettext 的 `.mo` | `pybabel compile` |
| **React 主界面**(看板/图表/SQL Lab,绝大多数人真正在看的) | `messages.json`(jed 1.x) | 官方前端构建的 npm 脚本 |

**只做第一半,用户感知到的就是"没汉化"。**

官方是在前端构建时生成那个 JSON 的,而我们的镜像是在官方镜像上加东西、
没有前端构建链。解法是 `apps/superset-image/build-language-packs.py`:
从同一份 `.po` 直接生成等价的 jed JSON,不需要 node、不改任何翻译内容。
转换逻辑先在运行中的 pod 里试过再固化进 Dockerfile——**4053 条真实中文条目**。

两个构建步骤都带 `test ... -gt 0` 断言:哪天上游改了目录结构,构建会失败,
而不是静默产出一个没有翻译的镜像(这个项目最忌讳的"看起来成功了")。

## 2026-08-27 晚补记:半个纠正 + 源码确认

使用方反馈"superset 现在已经基本汉化了,少数汉化不全的先不管了"。而当时
跑的镜像是 `21c233e`——**只编译了 `.mo`,没有 `messages.json`**。

第一反应是"那我关于 React 需要 messages.json 的判断错了"。去 Superset 源码
(`superset/translations/utils.py`)核实,结论是**没错**:

```python
def get_language_pack_filename(locale):
    if not locale or locale == "en":
        return DIR + "/empty_language_pack.json"
    return DIR + f"/{locale}/LC_MESSAGES/messages.json"   # ← React 读的就是这个
```

`get_language_pack()` 读不到就返回 None,前端回落英文。所以更合理的解释是:
**zhenghe 看到的"基本汉化"来自 `.mo` 覆盖的那部分(服务端渲染的页面),而
他说的"少数汉化不全"很可能正是 React 主界面那一块。**

不改成"我判断错了",也不硬说"我是对的"——把证据摆出来:源码明确写着前端读
`messages.json`,而当时的镜像里没有这个文件。带 language pack 的镜像
(`ee61412`)CI 已经构建好,这次把 tag 指过去,下次开机看"不全"的部分是不是
补齐了。**如果补齐了,说明这个分析成立;如果没变,说明我漏了别的东西**
——两种结果都有信息量。

**这里想记的方法教训是**:用户说"好了"的时候,不要顺着把自己之前的分析推翻,
也不要坚持。去找能分辨两种解释的证据(这次是上游源码),再决定改哪边。

## 2026-08-28 实机验证:分析成立

换成带 language pack 的镜像(`ee61412`)之后进 pod 验:

```
language pack 加载: 成功
条目数: 4054
  Dashboards -> 看板    Charts -> 图表
  Save -> 保存          Delete -> 删除
```

镜像里 **22 个 `.mo` + 23 个 `.json`** 都在。

**所以 08-27 那次「我判断错了吗」的悬案有答案了**:没错。当时 zhenghe 看到的
"基本汉化"来自 `.mo` 覆盖的服务端渲染部分,他说的"少数汉化不全"就是 React
主界面——补上 `messages.json` 之后那部分也有了。

值得留下的方法记录:那次我**没有顺着用户说的"好了"去推翻自己的分析,也没有
硬说自己对**,而是去 Superset 源码里找能分辨两种解释的证据
(`get_language_pack_filename()` 明确返回 `messages.json`),再据此决定改哪边。
这次的验证结果确认了那个做法。

## 还没做的

1. **加了 language pack 的镜像还没构建验证过。** 要实际登录看 React 界面
   是不是中文,不能只看"构建成功"和"文件存在"。
2. 其它组件(Airflow / Grafana / OpenMetadata)还是英文,没动。
