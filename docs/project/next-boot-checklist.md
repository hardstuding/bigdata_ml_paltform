# 下次开机要验的清单

> **这份文件只放「还没验的」。** 验完就从这里删掉,把结论写进
> [`capability-matrix.md`](capability-matrix.md) 对应那一行(带日期和证据),
> 过程如果值得留,写进 `docs/journal/`。
>
> **不要在这里堆已完成项。** 2026-09-02 这份文件长到 681 行、几乎全是
> 完成项时清过一次(历史见
> [verification-archive-2026-08](../journal/verification-archive-2026-08.md))
> —— 一份"打开之后要先翻过几百行才能找到待办"的清单,等于没有清单。

## 开机后先跑这个

```bash
./scripts/46-verify-p15.sh          # 自动化回归,一条条报 ✅/❌
./scripts/52-verify-sso-login.sh <用户名> <密码> superset   # 真的走完一遍登录
```

`46` 全部跳过会退出码 2,不会被当成通过 —— "什么都没验"的运行报成成功,
是这个项目栽过四次的模式。

`52` 的判据刻意不是 HTTP 200:SSO 坏掉的时候它也返回 200,只是又给你一张
登录页。这个区别让一个真实的登录 bug 藏了一周(见 CLAUDE.md
「程序化验证通过 ≠ 人能用」)。

## 还没验的

能力表里当前只剩两条,都是看板类:

| 能力 | 状态 | 怎么验 |
|---|---|---|
| 容量看板 | 🟡 未验证 | `platform/grafana-capacity-dashboard/` 6 个 panel。开 Grafana 看这几个 panel 出不出数,而不是看 Pod 是不是 Running |
| 管理驾驶舱 | 🟡 未验证 | 平台总览看板。同上;另外它依赖 Prometheus 的历史数据,2026-08-28 才开始攒,按月聚合的部分要等数据够 |

**判据统一是"业务结果"不是"组件状态"**:panel 有没有出数、数字对不对,
不是"Grafana 起来了"。

## 脚本验不了、必须真人点的

- **两个真实账号验越权**:A 打不开 B 的作业详情页
- **组权限申请的批准按钮**:platform-team 看得到、点了生效;非 platform-team
  拿不到按钮且直接 POST 接口被 403

## 结束前

对着 [`current-work.md`](current-work.md) 末尾那份清单过一遍。
