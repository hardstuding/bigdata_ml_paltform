#!/usr/bin/env python3
"""部署门禁:prod 的凭据相关配置里,占位值有没有换成真的。

**为什么是单独一个脚本,不并进渲染校验。** prod 这一档必须**始终能渲染**
—— CI 里有一步"三档都要能渲染出来",目的是让 prod 缺一个键在 CI 就报出来,
而不是等到真去部署那一档、人已经在等着上生产的时候才发现。如果把"占位符
没换"也做成渲染失败,prod 就永远渲染不了,那一步检查直接废掉。

所以分两层:

  渲染时  键在不在、值合不合法(scripts/render-environment-config.py)
  部署前  值是不是还是占位符(这个脚本)

**这个脚本刻意默认只查 prod。** local-lite/cloud-full 用占位/开发值是
正常的,对它们报错只会训练出"看到红就忽略"。

用法:
    python3 scripts/check-prod-secrets-ready.py          # 查 prod
    python3 scripts/check-prod-secrets-ready.py <env>    # 查指定环境
"""
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

# 占位符的标记。这个仓库里有两种写法,都收进来:
#   REPLACE-ME-...              新加的(ADR-089)
#   ...your-real-domain.example.com  早先 prod 配置里的约定
PLACEHOLDER_MARKS = ("REPLACE-ME", "your-real-domain.example.com", "example.com")

# 哪些键上生产前必须是真值。**只列凭据/安全相关的** —— 这个脚本是安全门禁,
# 不是"prod 配置完整性检查"(那是渲染那一层的事)。
GUARDED = {
    "openbao_kms_key_id": "OpenBao 的 KMS 自动解封密钥(ADR-089)",
    "openbao_kms_region": "OpenBao 的 KMS 区域",
    "openbao_kms_credentials_secret": "存放 KMS 访问凭据的 k8s Secret 名",
    "tls_acme_email": "Let's Encrypt 用它发证书过期提醒,填错等于放弃唯一的过期预警",
    "domain_suffix": "外部访问域名",
}


def main() -> int:
    env = sys.argv[1] if len(sys.argv) > 1 else "prod"
    cfg_path = REPO / "environments" / env / "config.yaml"
    if not cfg_path.exists():
        print(f"!! 没有 environments/{env}/config.yaml", file=sys.stderr)
        return 1
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    # **每个受管键都只在"它那条路真的启用了"的时候才查。**
    #
    # 第一版没这么写,结果 cloud-full 被 tls_acme_email 拦下 —— 而那一档
    # 走的是自签证书,本来就不该有 ACME 邮箱。一个会对正确配置报错的门禁,
    # 和没有门禁一样糟:它训练出的是"看到红就忽略"。
    guarded = dict(GUARDED)
    if cfg.get("seal_mode") != "kms":
        for k in ("openbao_kms_key_id", "openbao_kms_region",
                  "openbao_kms_credentials_secret"):
            guarded.pop(k, None)
    if cfg.get("tls_issuer_mode") != "acme":
        guarded.pop("tls_acme_email", None)

    problems = []
    for key, why in guarded.items():
        val = str(cfg.get(key, ""))
        if not val:
            problems.append(f"{key}:没配 —— {why}")
            continue
        if any(m in val for m in PLACEHOLDER_MARKS):
            problems.append(f"{key} = {val!r}\n      还是占位值 —— {why}")

    if problems:
        print(f"environments/{env}/config.yaml 里还有 {len(problems)} 处没换成真值:\n",
              file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(f"\n  这些是**上生产的硬门禁**,不是建议。其中 seal_mode=kms 那几个尤其要紧:\n"
              f"  没配真的 KMS 就部署,OpenBao 会起不来 —— 而更坏的情况是有人为了让它\n"
              f"  起来,把 seal_mode 改回 dev-autounseal,那样生产凭据库的解封密钥\n"
              f"  就放在同一个集群的 k8s Secret 里了(见 ADR-089)。", file=sys.stderr)
        return 1

    print(f"environments/{env}/config.yaml:{len(guarded)} 个受管键都不是占位值。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
