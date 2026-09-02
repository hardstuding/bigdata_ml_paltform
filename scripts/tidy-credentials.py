#!/usr/bin/env python3
"""把 secrets/generated-credentials.txt 收敛成"只剩当前有效的那些"。

**为什么需要**:`00-generate-secrets.sh` 是**追加**写的(`>> $OUT_FILE`),
每跑一次就往文件末尾加一段。三周下来的结果是 2026-08-28 盘点时发现的那样
——"secrets 里还是太多无效的账号密码了":同一个凭据出现 6 次、6 个不同的
密码,中间夹着几十行只有时间戳、底下什么都没有的空段(那次运行所有 Secret
都已存在、一条都没新建)。

**哪一条是有效的**:`ensure_secret` 在 Secret 已存在时**直接 return,不写
这个文件**,所以文件里每出现一次就意味着"那一刻真的新建了一个 Secret"
—— 集群重建过(2026-08-22 推倒重建)之后旧的那些就全失效了。因此规则是
**同名取最后一条**。

**不删原文件**:重命名成 `.archive`,而不是就地删。这是本机唯一一份凭据
记录,如果上面那个"最后一条有效"的判断在某个边角上不成立(比如手工 reset
过而没有记进来),原始记录还在。

**这个脚本不连集群** —— 它只做去重。真正确认"这些值和集群里的一致"要开机
之后跑 `scripts/check-manual-credentials.sh`。

用法:
  python3 scripts/tidy-credentials.py            # 预览会保留/丢弃什么
  python3 scripts/tidy-credentials.py --write    # 真的写
"""
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CRED = REPO / "secrets" / "generated-credentials.txt"


TS_RE = re.compile(r"^20\d\d-\d\d-\d\dT[\d:]+Z\s+")


def parse(text):
    """返回 [(去重键, 整行)],按出现顺序。跳过注释和空行。

    **去重键不能只取第一个词。** 第一版就是那么写的,预览时发现
    `keycloak-platform-realm` 有 10 条 —— 而它们是 **5 个不同用户**
    (admin / dev001 / analyst001 / algo001 / ceo001),按名字合并会
    **丢掉 4 个人的密码**。所以 realm 账号那种行,键是「名字 + 用户名」。

    另外有些行前面带时间戳(手工补记的那几条),要先剥掉,否则时间戳
    会被当成名字。
    """
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = TS_RE.sub("", s)          # 剥掉行首时间戳
        parts = s.split()
        name = parts[0].rstrip(":")
        # `<realm> <用户> / <密码>` 这种:用户名也是键的一部分
        if len(parts) >= 3 and parts[2] == "/":
            key = f"{name} {parts[1]}"
        else:
            key = name
        out.append((key, s))
    return out


def main() -> int:
    write = "--write" in sys.argv
    if not CRED.exists():
        print(f"{CRED} 不存在,没有要整理的。")
        return 0

    raw = CRED.read_text()
    entries = parse(raw)
    total_lines = len(raw.splitlines())

    # 同名取最后一条
    latest = {}
    for name, line in entries:
        latest[name] = line
    dropped = len(entries) - len(latest)

    print(f"原文件 {total_lines} 行,其中凭据行 {len(entries)} 条")
    print(f"去重后 {len(latest)} 条,丢弃 {dropped} 条被同名新值取代的旧记录\n")
    for name in sorted(latest):
        n = sum(1 for e, _ in entries if e == name)
        mark = f"(原有 {n} 条,保留最后一条)" if n > 1 else ""
        print(f"  {name} {mark}")

    if not write:
        print("\n这是预览。加 --write 才真的改。")
        return 0

    archive = CRED.with_suffix(".txt.archive")
    shutil.copy2(CRED, archive)
    os.chmod(archive, 0o600)

    header = (
        f"# 当前有效的凭据 —— {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} "
        f"由 scripts/tidy-credentials.py 整理\n"
        "#\n"
        "# **不要提交到 git**(secrets/ 已经在 .gitignore 里)。\n"
        "#\n"
        "# 整理规则:同一个凭据名只保留最后一条。00-generate-secrets.sh 在\n"
        "# Secret 已存在时不会写这个文件,所以每出现一次都意味着那一刻真的\n"
        "# 新建了一个 Secret;集群重建之后旧的就失效了。\n"
        f"# 整理前的完整历史在 {archive.name}(同样不进 git)。\n"
        "#\n"
        "# 这里的值**没有和集群核对过** —— 开机后跑\n"
        "# scripts/check-manual-credentials.sh 才能确认一致。\n"
    )
    CRED.write_text(header + "\n".join(latest[k] for k in sorted(latest)) + "\n")
    os.chmod(CRED, 0o600)
    print(f"\n已写入 {CRED}({len(latest)} 条),权限收紧到 600")
    print(f"原文件备份为 {archive.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
