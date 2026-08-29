# 工具图标

来自 [Simple Icons](https://github.com/simple-icons/simple-icons),**CC0 1.0
(公有领域)**,可以直接 vendored 进仓库、随镜像分发,不需要额外授权。

文件名对应 `app.py` 里 `TOOLS` 每一项的 `logo` 字段。**没有对应文件的工具
会回退成首字母方块** —— 这是有意的:自建的那几个工具(权限申请、建表注册、
门户自己)本来就没有官方标识,硬凑一个反而不如一个规整的字母块。

图标是单色的,页面里用 `fill: currentColor` 跟随主题(深色模式下自动变亮),
所以**不要**改成带颜色的版本,那样在深色背景上会糊。

更新某个图标:

```bash
curl -o <名字>.svg \
  https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/<slug>.svg
```

slug 见 Simple Icons 仓库的 `icons/` 目录。
