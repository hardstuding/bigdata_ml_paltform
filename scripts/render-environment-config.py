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
}


def load_config(env: str) -> dict:
    config_path = REPO_ROOT / "environments" / env / "config.yaml"
    if not config_path.exists():
        print(f"!! 找不到 {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


def render_text(text: str, config: dict) -> str:
    return (
        text.replace("{{DOMAIN_SUFFIX}}", config["domain_suffix"])
        .replace("{{HTTP_PORT_SUFFIX}}", config["http_port_suffix"])
        .replace("{{HTTPS_PORT_SUFFIX}}", config["https_port_suffix"])
        .replace("{{EXTERNAL_SCHEME}}", config["external_scheme"])
    )


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
            rendered = render_text(template_file.read_text(), config)
            rendered_count += 1

            if not target_file.exists():
                print(f"!! {target_file} 不存在,先手动确认这是不是要新建的文件", file=sys.stderr)
                ok = False
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

    # **先把 config 校验完整,再动任何文件。** 2026-08-21 真实踩到:
    # environments/prod/config.yaml 有 domain_suffix 但没有 enabled_components,
    # 跑 `render-environment-config.py prod` 时第一步 render_templates 已经
    # 把 platform/apps/*.yaml 里的域名全替换成了 prod 的占位域名,第二步
    # render_components 才因为缺 enabled_components 失败——工作区被留在
    # "一半 prod 一半 cloud-full"的混合状态,而且没有任何提示。对着一个正在
    # 服务 cloud-full 的工作区,这等于悄悄把部署配置改脏了。
    # 校验前置之后,配置不完整就在写任何文件之前直接退出。
    missing = [k for k in ("domain_suffix", "http_port_suffix", "https_port_suffix",
                           "external_scheme") if k not in config]
    if missing:
        print(f"!! environments/{env}/config.yaml 缺这些必填项: {missing}", file=sys.stderr)
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
