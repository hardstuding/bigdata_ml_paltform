#!/usr/bin/env python3
"""把 environments/<env>/config.yaml 渲染成实际部署的文件,做两件独立的事:

1. **变量替换**——`templates/` 目录下的模板文件用 domain_suffix/
   http_port_suffix/https_port_suffix/external_scheme 这几个值渲染。这几个
   值只在 config.yaml 里出现一次,不再散落硬编码在每个组件的 manifest 里。
   见 environments/cloud-full/config.yaml 顶部注释:2026-08-16 一次真实的
   SSO 登录 404/502 连环事故之后建的,背景是这几个值当时散落在 9 个文件里,
   改一处不代表其他跟着变。

2. **组件选择**(2026-08-20 新增,ADR-057 第三批/`docs/BACKLOG.md` 1.1)——
   `apps/components/` 是所有组件定义的唯一源码(43 个,以前分散在
   `apps/definitions/`(直接常驻的)和 `environments/<env>/
   pending-definitions/`(park 着的)两处,靠人工 `git mv` 表达"这个环境
   要不要这个组件",这个仓库已经因为这个机制吃过亏——2026-08-14 文档
   审计发现 Spark Operator/Airflow 早就常驻好几天,但没有任何文档/配置
   反映这件事,只能翻 `ls` 现场确认)。现在"这个环境要哪些组件"是
   `config.yaml` 里的 `enabled_components` 这一份显式列表,`apps/
   definitions/` 变成 100% 生成产物——环境里所有组件,变量替换 + 组件
   过滤是同一次运行、同一个真相来源,不是两条平行的机制。
   `pending-definitions/` 这个目录机制正式退役,不要再用。

模板目录结构镜像实际部署路径,渲染时去掉 `templates/` 前缀(`apps/
components/` 是例外,见下面第 2 部分的单独说明):
  templates/platform-apps/X.yaml         ->  platform/apps/X.yaml
  templates/platform-bootstrap/X.yaml    ->  platform/bootstrap/X.yaml
  templates/scripts/X.sh                 ->  scripts/X.sh

占位符是 `{{DOMAIN_SUFFIX}}`/`{{HTTP_PORT_SUFFIX}}`/`{{HTTPS_PORT_SUFFIX}}`/
`{{EXTERNAL_SCHEME}}`,简单字符串替换,不是完整模板引擎(这个项目的规模
不需要 Jinja2 那一整套,几个占位符字符串替换就够用,minimal 原则)。
`apps/components/` 下的文件不一定含占位符(43 个里只有少数几个真的需要
按环境改地址),不含占位符的文件渲染前后内容不变,这是正常情况,不是
"这个文件没生效"。

`{{EXTERNAL_SCHEME}}` 只用在"浏览器/外部客户端会直接访问"的地址上
(域名本身,不管带不带端口后缀)——local-lite/cloud-full 是 `http`
(自签证书内部不受信任,ingress 层就没强制走 TLS),prod 应该是 `https`
(真实 CA 证书)。`.svc.cluster.local` 这种纯集群内部 Service DNS 不用
这个占位符,永远写死 `http://`——这类调用根本不经过 ingress,和外部
访问用的是不是 TLS 无关。

用法:
  python3 scripts/render-environment-config.py cloud-full           # 渲染,写回文件
  python3 scripts/render-environment-config.py cloud-full --check   # 只检查是否一致,不写文件,
                                                                        退出码非 0 表示有漂移(适合接进 CI)

**风险提醒(2026-08-20,测试 local-lite-toggle-heavy.sh 时真实踩到过)**:
`apps/definitions/` 在这份机制下同一时刻只能代表**一个**环境的渲染结果
——这在旧的 `pending-definitions/` 机制下也是事实,不是这次新引入的问题,
但新机制下更容易在无意间触发:只要跑一次 `render-environment-config.py
local-lite`,`apps/definitions/` 就会被覆盖成 local-lite 那份更小的
enabled_components 列表,如果这个工作区实际对应的是 cloud-full 的活的
部署状态,这一下会把 cloud-full 独有的组件文件删掉(本地文件层面,还没
git push/触发 ArgoCD 同步之前不会动到真实集群,但下一次 push 就会)。
对着一个正在服务 cloud-full 的工作区,不要顺手跑 `... local-lite` 之类
针对别的环境的渲染命令;真要在本机测试 local-lite 相关改动,跑完之后、
提交之前,记得跑一次 `... cloud-full --check` 确认没有把 cloud-full 的
渲染结果带歪。
"""
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
COMPONENTS_DIR = REPO_ROOT / "apps" / "components"
DEFINITIONS_DIR = REPO_ROOT / "apps" / "definitions"

DIR_MAP = {
    "platform-apps": REPO_ROOT / "platform" / "apps",
    "platform-bootstrap": REPO_ROOT / "platform" / "bootstrap",
    "scripts": REPO_ROOT / "scripts",
    # 裸 manifest 类组件里需要按环境分档的,把源文件放进 templates/,
    # 生成产物仍然落在 ArgoCD 原本读的那个路径上——**Application 的
    # `path:` 完全不用改**。ADR-059 一开始判断"裸 manifest 覆盖不到、需要
    # 架构级改动",后来发现现有机制这样扩展就够了,那个判断过重,已修正。
    "apps-postgres-manifests": REPO_ROOT / "apps" / "postgres" / "manifests",
    "platform-cert-manager-issuers": REPO_ROOT / "platform" / "cert-manager-issuers" / "manifests",
    "platform-alertmanager-notification": REPO_ROOT / "platform" / "alertmanager-notification" / "manifests",
    "apps-kafka-manifests": REPO_ROOT / "apps" / "kafka" / "manifests",
    # 2026-08-22 新增(docs/decisions/062-flink-streaming-pipeline.md):
    # FlinkDeployment 的 resources/并行度、CronJob 的 resources 三个环境
    # 不同,同样走"源文件挪进 templates/,渲染产物落回 ArgoCD 原本读的
    # 路径"这条已经验证过的路(见上面 Postgres/cert-manager-issuers 那几条
    # 的注释,这次不是新机制,是同一个机制第三/四次使用)。
    "apps-flink-streaming-demo-manifests": REPO_ROOT / "apps" / "flink-streaming-demo" / "manifests",
    "apps-kafka-producer-manifests": REPO_ROOT / "apps" / "kafka-producer" / "manifests",
    "apps-postgres-backup-manifests": REPO_ROOT / "apps" / "postgres-backup" / "manifests",
    # 2026-08-23(ADR-064):Kueue 的队列配额三个环境差别巨大
    "apps-kueue-manifests": REPO_ROOT / "apps" / "kueue" / "manifests",
    "apps-schema-registry-manifests": REPO_ROOT / "apps" / "schema-registry" / "manifests",
}

# 模板文件第一行可以写 `# render-if: <config键> == <值>`,表示"只有当前
# 环境的 config.yaml 里这个键等于这个值时才生成这个文件,否则把目标文件
# 删掉"。存在的理由是有些东西不是"同一份内容换几个数字"(那用占位符就够
# 了),而是**互斥的几选一**——cert-manager 的 ClusterIssuer 就是典型:
# local-lite 要自签、prod 要 ACME,两个 spec 结构完全不同,没法用占位符
# 拼出来,而且同时存在是错的(ACME issuer 在没有真实域名的环境里会一直
# 报错重试)。
#
# 条件不成立时**主动删除**目标文件,而不是放着不管——放着不管的话,从
# prod 切回 cloud-full 会留下一个 ACME issuer 的残留文件,ArgoCD 照样
# 会把它同步上去,正是这个项目最忌讳的"看起来切干净了其实没有"。
_RENDER_IF_RE = re.compile(r"^# render-if:\s*([a-z0-9_]+)\s*==\s*(\S+)\s*$")


def load_config(env: str) -> dict:
    config_path = REPO_ROOT / "environments" / env / "config.yaml"
    if not config_path.exists():
        print(f"!! 找不到 {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


RESOURCE_PROFILES = REPO_ROOT / "environments" / "resource-profiles.yaml"
_RES_RE = re.compile(r"\{\{RES:([a-z0-9_]+)\}\}")


def load_resource_profile(env: str) -> dict:
    """取这个环境的规格档(见 environments/resource-profiles.yaml 顶部说明)。

    三个环境并排写在同一个文件里,缺键直接报错不给默认值兜底——和这个项目
    其它地方"不用默认值悄悄兜底"的一贯做法一致。
    """
    if not RESOURCE_PROFILES.exists():
        print(f"!! 找不到 {RESOURCE_PROFILES}", file=sys.stderr)
        sys.exit(1)
    profiles = yaml.safe_load(RESOURCE_PROFILES.read_text()) or {}
    if env not in profiles:
        print(
            f"!! environments/resource-profiles.yaml 里没有 `{env}` 这一档。"
            "每个环境都必须显式给出规格,不接受隐式默认值。",
            file=sys.stderr,
        )
        sys.exit(1)

    # 三档的键集合必须完全一致。防的是"给 prod 加了个新可调项,忘了给
    # local-lite/cloud-full 补"——那种情况下别的环境要等到真去渲染时才
    # 报错,而 CI 只跑 cloud-full 一档,很可能一直发现不了。
    keysets = {name: set((vals or {}).keys()) for name, vals in profiles.items()}
    union = set().union(*keysets.values()) if keysets else set()
    incomplete = {n: sorted(union - ks) for n, ks in keysets.items() if union - ks}
    if incomplete:
        print("!! environments/resource-profiles.yaml 三档的键不一致,缺的是:", file=sys.stderr)
        for n, miss in incomplete.items():
            print(f"   {n}: 缺 {miss}", file=sys.stderr)
        sys.exit(1)

    return profiles[env] or {}


def render_text(text: str, config: dict) -> str:
    text = (
        text.replace("{{DOMAIN_SUFFIX}}", config["domain_suffix"])
        .replace("{{HTTP_PORT_SUFFIX}}", config["http_port_suffix"])
        .replace("{{HTTPS_PORT_SUFFIX}}", config["https_port_suffix"])
        .replace("{{EXTERNAL_SCHEME}}", config["external_scheme"])
    )

    # TLS/ACME 相关的占位符只有走 ACME 那一档的环境才用得上,所以**不放进
    # 顶部那份"每个环境都必须有"的必填校验**——local-lite 的 config.yaml 里
    # 塞一个假的 ACME 邮箱纯属噪音。代价是缺键要在这里报错,报错信息里要
    # 说清楚该去哪补,不能只抛一个 KeyError。
    for key in ("tls_acme_server", "tls_acme_email",
                "backup_s3_endpoint", "backup_s3_bucket", "backup_s3_secret"):
        token = "{{" + key.upper() + "}}"
        if token not in text:
            continue
        if key not in config:
            print(
                f"!! 模板里用了 {token},但 environments/{config['_env']}/config.yaml "
                f"里没有 `{key}`(走 ACME 签发真实证书的环境才需要这两个键)",
                file=sys.stderr,
            )
            sys.exit(1)
        text = text.replace(token, str(config[key]))

    # 规格分档占位符 {{RES:key}}。用不存在的 key 直接报错退出,不静默留着
    # 原样——那样会渲染出一个字面量是 "{{RES:xxx}}" 的 YAML,部署上去才
    # 发现,正是这个项目最忌讳的"看起来成功了"。
    profile = config.get("_resource_profile", {})

    def _sub(m):
        key = m.group(1)
        if key not in profile:
            print(
                f"!! 组件里用了 {{{{RES:{key}}}}},但 environments/resource-profiles.yaml "
                f"的 `{config.get('_env')}` 这一档里没有这个键",
                file=sys.stderr,
            )
            sys.exit(1)
        return str(profile[key])

    return _RES_RE.sub(_sub, text)


def render_templates(config: dict, check_only: bool) -> tuple[bool, int]:
    """第 1 部分:templates/ 下的其它模板(platform-apps/platform-bootstrap/
    scripts),原有逻辑不变——这些文件的目标路径本来就该已经存在,不存在
    说明配置写错了地方,直接报错,不像组件选择那样允许"环境没启用就不
    存在"。
    """
    ok = True
    rendered_count = 0
    for template_root_name, target_dir in DIR_MAP.items():
        template_root = TEMPLATES_DIR / template_root_name
        if not template_root.exists():
            continue
        for template_file in template_root.rglob("*"):
            if not template_file.is_file():
                continue
            rel = template_file.relative_to(template_root)
            target_file = target_dir / rel
            raw = template_file.read_text()

            # 条件生成(见 _RENDER_IF_RE 上面的注释)
            conditional = False
            first_line = raw.split("\n", 1)[0]
            m = _RENDER_IF_RE.match(first_line)
            if m:
                conditional = True
                key, want = m.group(1), m.group(2)
                if key not in config:
                    print(
                        f"!! {template_file.relative_to(REPO_ROOT)} 的 render-if 用了 `{key}`,"
                        f"但 environments/{config['_env']}/config.yaml 里没有这个键",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                if str(config[key]) != want:
                    if target_file.exists():
                        if check_only:
                            print(f"!! {target_file.relative_to(REPO_ROOT)}: 当前环境不该有这个文件,漂移了")
                            ok = False
                        else:
                            target_file.unlink()
                            print(f"{target_file.relative_to(REPO_ROOT)}: 当前环境不适用,已删除")
                    continue
                raw = raw.split("\n", 1)[1] if "\n" in raw else ""

            rendered = render_text(raw, config)
            rendered_count += 1

            if not target_file.exists():
                if not conditional:
                    print(f"!! {target_file} 不存在,先手动确认这是不是要新建的文件", file=sys.stderr)
                    ok = False
                    continue
                # 条件生成的文件"不存在"是正常的(上一次渲染的是别的环境),
                # 直接建出来,不当成错误。
                if check_only:
                    print(f"!! {target_file.relative_to(REPO_ROOT)}: 当前环境应该有这个文件但缺了,漂移了")
                    ok = False
                    continue
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(rendered)
                print(f"{target_file.relative_to(REPO_ROOT)}: 已生成(条件生成)")
                continue

            current = target_file.read_text()
            if current == rendered:
                print(f"{target_file.relative_to(REPO_ROOT)}: 已经一致")
                continue

            if check_only:
                print(f"!! {target_file.relative_to(REPO_ROOT)}: 和模板渲染结果不一致,漂移了")
                ok = False
                continue

            target_file.write_text(rendered)
            print(f"{target_file.relative_to(REPO_ROOT)}: 已重新生成")

    if rendered_count == 0:
        print("!! templates/ 下没有找到任何模板文件", file=sys.stderr)
        sys.exit(1)

    return ok, rendered_count


GENERATED_HEADER = (
    "# 这个文件是自动生成的(python3 scripts/render-environment-config.py\n"
    "# <env>),源头是 apps/components/{name} +\n"
    "# environments/<env>/config.yaml 里的 enabled_components 列表——改动\n"
    "# 请改 apps/components/{name},不要直接改这份生成结果(下次渲染会被\n"
    "# 覆盖)。这个组件要不要在这个环境启用,改 config.yaml 的\n"
    "# enabled_components 列表,不要手动删/加这个文件。见\n"
    "# docs/decisions/057-architecture-review-2026-08-19.md 第三批。\n"
)


def render_components(config: dict, check_only: bool) -> bool:
    """第 2 部分:组件选择——按 config['enabled_components'] 这份显式清单,
    从 apps/components/ 挑文件、做变量替换、生成到 apps/definitions/。

    和上面 render_templates() 的关键差异:这里 apps/definitions/ 整个目录
    都是生成产物,文件"应该存在"完全由 enabled_components 决定,不是
    "本来就该在那"——所以这里要处理三种情况,上面那个函数不需要处理:
    新增(启用了但还没生成过)、更新(内容变了)、删除(以前生成过,现在
    不在启用列表里了,得清掉,不然 ArgoCD 会继续同步一个"环境本不该有"
    的组件,和当初 pending-definitions/ 想解决的问题一模一样,只是换成
    了残留文件而不是残留目录条目)。
    """
    enabled = config.get("enabled_components")
    if enabled is None:
        print(
            "!! config.yaml 里没有 enabled_components 这个列表——"
            "每个环境都必须显式声明启用哪些组件,不接受隐式默认值",
            file=sys.stderr,
        )
        return False
    enabled = set(enabled)

    known = {p.name for p in COMPONENTS_DIR.glob("*.yaml")}
    unknown = enabled - known
    if unknown:
        print(
            f"!! enabled_components 里有 apps/components/ 下不存在的文件: {sorted(unknown)}",
            file=sys.stderr,
        )
        return False

    ok = True
    DEFINITIONS_DIR.mkdir(parents=True, exist_ok=True)

    for name in sorted(enabled):
        src = COMPONENTS_DIR / name
        target = DEFINITIONS_DIR / name
        rendered = GENERATED_HEADER.format(name=name) + render_text(src.read_text(), config)

        if not target.exists():
            if check_only:
                print(f"!! apps/definitions/{name}: 应该启用但还没生成")
                ok = False
                continue
            target.write_text(rendered)
            print(f"apps/definitions/{name}: 已新建")
            continue

        current = target.read_text()
        if current == rendered:
            print(f"apps/definitions/{name}: 已经一致")
            continue

        if check_only:
            print(f"!! apps/definitions/{name}: 和组件源码渲染结果不一致,漂移了")
            ok = False
            continue

        target.write_text(rendered)
        print(f"apps/definitions/{name}: 已重新生成")

    # 清理:apps/definitions/ 里存在、但不在这次 enabled_components 里的
    # 文件——说明这个组件被这个环境的配置停用了,生成产物也该跟着消失。
    stale = {p.name for p in DEFINITIONS_DIR.glob("*.yaml")} - enabled
    for name in sorted(stale):
        if check_only:
            print(f"!! apps/definitions/{name}: 不在 enabled_components 里,应该被清理")
            ok = False
            continue
        (DEFINITIONS_DIR / name).unlink()
        print(f"apps/definitions/{name}: 已删除(不在 enabled_components 里)")

    return ok


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/render-environment-config.py <env> [--check]", file=sys.stderr)
        sys.exit(1)
    env = sys.argv[1]
    check_only = "--check" in sys.argv
    config = load_config(env)
    config["_env"] = env
    config["_resource_profile"] = load_resource_profile(env)

    # **先把 config 校验完整,再动任何文件。** 2026-08-21 真实踩到:
    # environments/prod/config.yaml 有 domain_suffix 但没有 enabled_components,
    # 跑 `render-environment-config.py prod` 时第一步 render_templates 已经
    # 把 platform/apps/*.yaml 里的域名全替换成了 prod 的占位域名,第二步
    # render_components 才因为缺 enabled_components 失败——工作区被留在
    # "一半 prod 一半 cloud-full"的混合状态,而且没有任何提示。对着一个正在
    # 服务 cloud-full 的工作区,这等于悄悄把部署配置改脏了。
    # 校验前置之后,配置不完整就在写任何文件之前直接退出。
    missing = [k for k in ("domain_suffix", "http_port_suffix", "https_port_suffix",
                           "external_scheme", "tls_issuer_mode") if k not in config]
    if missing:
        print(f"!! environments/{env}/config.yaml 缺这些必填项: {missing}", file=sys.stderr)
        sys.exit(1)
    if config["tls_issuer_mode"] not in ("selfsigned", "acme"):
        print(
            f"!! environments/{env}/config.yaml 的 tls_issuer_mode 只能是 selfsigned 或 acme,"
            f"现在是 `{config['tls_issuer_mode']}`",
            file=sys.stderr,
        )
        sys.exit(1)
    if config.get("enabled_components") is None:
        print(
            f"!! environments/{env}/config.yaml 里没有 enabled_components 这个列表,"
            "不知道这个环境要启用哪些组件。\n"
            "   **没有写任何文件就退出了**——不会把工作区留在半渲染的混合状态。\n"
            "   参考 environments/cloud-full/config.yaml 的写法补上这个列表。",
            file=sys.stderr,
        )
        sys.exit(1)

    templates_ok, _ = render_templates(config, check_only)
    components_ok = render_components(config, check_only)

    sys.exit(0 if (templates_ok and components_ok) else 1)


if __name__ == "__main__":
    main()
