"""连接封装:Trino / MLflow / 对象存储。

这个模块的全部价值,是让下面这段在 notebook 里能直接跑,不用装 client、
不用查连接串、不用问别人账号密码放哪:

    from platform_sdk import query
    df = query("select * from iceberg.demo.orders limit 10")

`docs/usage-guide.md` "交互式开发 / Notebook" 一节把"没有自动连 Trino、
没有自动带凭据"记录成一个**真实的产品差距**;这个文件就是那个差距的解法。
"""

from __future__ import annotations

import os
from typing import Any

from . import config


# ------------------------------------------------------------------ Trino


def acting_user() -> str | None:
    """当前这段代码是"代表谁"在跑 —— 拿不到就返回 None。

    **为什么需要这个概念**:SDK 连 Trino 用的是**服务账号**,而服务账号在
    OPA 策略里是无条件放行的(见 apps/opa/manifests/policy-configmap.yaml)。
    如果就这么查下去,行列级权限对 notebook 完全不生效 —— 分析师在 Superset
    里被脱敏的手机号,在 notebook 里能直接查出明文。Superset 那条路
    2026-08-26 就用 impersonation 修好了(ADR-074),SDK 这条路一直没修。

    取值优先级:
      1. `PLATFORM_ACTING_USER` —— 显式指定,给"我知道自己在代表谁"的场景
         (比如一个服务代表某个用户跑批)。
      2. `JUPYTERHUB_USER` —— JupyterHub 给每个 notebook pod 自动注入的标准
         变量,值就是登录用户名。**这是 notebook 场景不用任何配置就能生效
         的关键**。
    两个都没有(比如定时作业)就返回 None,连接退回"以服务账号身份查"——
    那种场景本来就没有"当前用户"这个概念。
    """
    return os.environ.get("PLATFORM_ACTING_USER") or os.environ.get("JUPYTERHUB_USER") or None


def trino_connection(
    catalog: str | None = None,
    schema: str | None = None,
    user: str | None = None,
    password: str | None = None,
    act_as: str | None = None,
):
    """建一个 Trino 连接(DBAPI 连接对象)。

    凭据来源优先级:显式传参 > 环境变量。没有内置默认账号——平台上每个
    组件都有自己独立的 Trino 服务账号(ADR-021 的既定原则,方便单独追溯和
    吊销),SDK 不该替使用者选一个。

    认证方式是 HTTP Basic。Trino 那边配的是 file 类型的 PASSWORD 认证器
    (`password-authenticator.config-files`,见 apps/definitions/trino.yaml),
    协议层面就是 Basic Auth——这一点在 apps/dbt-demo/project/profiles.yml
    的注释里已经查证过(dbt-trino 把它叫 "ldap" 只是历史命名)。
    """
    # 延迟导入:只想用 MLflow 的人不该因为没装 trino 客户端就 import 失败。
    from trino.auth import BasicAuthentication
    from trino.dbapi import connect

    user = user or config.require(
        "PLATFORM_TRINO_USER",
        "这是 Trino 的服务账号名(比如 superset_service),由 "
        "scripts/00-generate-secrets.sh 生成在 trino-service-account 这个 Secret 里。",
    )
    password = password or config.require(
        "PLATFORM_TRINO_PASSWORD",
        "对应 PLATFORM_TRINO_USER 那个账号的密码,同样在 "
        "trino-service-account 这个 Secret 里。",
    )

    # 身份代理:让 Trino 把这次查询当成 `acting` 这个人发起的,权限也按他算。
    # 用的是 Trino 原生的 `X-Trino-Authorization-User` 头,和 Superset 走的是
    # 同一条路(ADR-074)。服务账号必须在 OPA 的 impersonation_allowed_accounts
    # 里,否则 Trino 直接拒绝——**这是有意的**:能代理别人是一项特权,不该
    # 因为某个账号"是服务账号"就自动拥有。
    headers = {}
    acting = act_as or acting_user()
    if acting and acting != user:
        headers["X-Trino-Authorization-User"] = acting

    return connect(
        host=config.trino_host(),
        port=config.trino_port(),
        user=user,
        catalog=catalog or config.trino_catalog(),
        schema=schema or config.trino_schema(),
        http_scheme=config.trino_scheme(),
        auth=BasicAuthentication(user, password),
        verify=config.trino_verify(),
        http_headers=headers or None,
    )


def query(sql: str, catalog: str | None = None, schema: str | None = None,
          act_as: str | None = None):
    """跑一条 SQL,尽量返回 pandas DataFrame,没装 pandas 就返回 (列名, 行) 元组。

    不硬依赖 pandas 是有意的:调度任务里跑一条 DDL/INSERT 不该被迫装 pandas。
    交互式场景基本都有 pandas(统一镜像里带了),会走 DataFrame 这条路径。
    """
    with trino_connection(catalog=catalog, schema=schema, act_as=act_as) as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        # cursor.description 在 DDL 语句上可能是 None,要防一手
        columns = [d[0] for d in cursor.description] if cursor.description else []

    try:
        import pandas as pd
    except ImportError:
        return columns, rows
    return pd.DataFrame(rows, columns=columns)


# ----------------------------------------------------------------- MLflow


def mlflow_setup(experiment: str | None = None):
    """配好 MLflow 并(可选)选定实验,返回 mlflow 模块本身。

    用法:

        from platform_sdk import mlflow_setup
        mlflow = mlflow_setup("my-experiment")
        with mlflow.start_run():
            mlflow.log_metric("acc", 0.9)

    注意这里**故意不调用 `mlflow.set_tracking_uri()`**——MLflow 客户端自己
    就会读 `MLFLOW_TRACKING_URI`。这里只在该变量缺失时补上集群内默认值,
    保持"环境变量是唯一事实来源"这个原则,避免出现"代码里设了一个、环境
    变量里是另一个,排查时看不出以哪个为准"的情况。
    """
    import os

    import mlflow

    os.environ.setdefault("MLFLOW_TRACKING_URI", config.mlflow_tracking_uri())
    os.environ.setdefault("MLFLOW_S3_ENDPOINT_URL", config.s3_endpoint_url())

    if experiment:
        mlflow.set_experiment(experiment)
    return mlflow


# ---------------------------------------------------------- 对象存储 (MinIO)


def s3_client() -> Any:
    """boto3 的 S3 客户端,已指向平台的 MinIO。

    凭据走标准的 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY,和 MLflow 上传
    artifact 用的是同一组变量,不重复发明。
    """
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=config.s3_endpoint_url(),
        aws_access_key_id=config.require(
            "AWS_ACCESS_KEY_ID",
            "MinIO 的 access key,集群里通常来自 minio-root 这个 Secret 的 rootUser。",
        ),
        aws_secret_access_key=config.require(
            "AWS_SECRET_ACCESS_KEY",
            "MinIO 的 secret key,来自 minio-root 这个 Secret 的 rootPassword。",
        ),
    )
