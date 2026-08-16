#!/usr/bin/env python3
"""把 templates/ 目录下的模板文件,用 environments/<env>/config.yaml 里的
值渲染成实际部署的文件——domain_suffix/http_port_suffix/https_port_suffix
这几个值只在 config.yaml 里出现一次,不再散落硬编码在每个组件的 manifest
里。见 environments/cloud-full/config.yaml 顶部注释:2026-08-16 一次真实
的 SSO 登录 404/502 连环事故之后建的,背景是这几个值当时散落在 9 个
文件里,改一处不代表其他跟着变。

模板目录结构镜像实际部署路径,渲染时去掉 `templates/` 前缀:
  templates/apps-definitions/trino.yaml  ->  apps/definitions/trino.yaml
  templates/platform-apps/X.yaml         ->  platform/apps/X.yaml
  templates/platform-bootstrap/X.yaml    ->  platform/bootstrap/X.yaml
  templates/scripts/X.sh                 ->  scripts/X.sh

占位符是 `{{DOMAIN_SUFFIX}}`/`{{HTTP_PORT_SUFFIX}}`/`{{HTTPS_PORT_SUFFIX}}`,
简单字符串替换,不是完整模板引擎(这个项目的规模不需要 Jinja2 那一整套,
三个占位符字符串替换就够用,minimal 原则)。

用法:
  python3 scripts/render-environment-config.py cloud-full           # 渲染,写回文件
  python3 scripts/render-environment-config.py cloud-full --check   # 只检查是否一致,不写文件,
                                                                        退出码非 0 表示有漂移(适合接进 CI)
"""
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"

DIR_MAP = {
    "apps-definitions": REPO_ROOT / "apps" / "definitions",
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
    )


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/render-environment-config.py <env> [--check]", file=sys.stderr)
        sys.exit(1)
    env = sys.argv[1]
    check_only = "--check" in sys.argv
    config = load_config(env)

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

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
