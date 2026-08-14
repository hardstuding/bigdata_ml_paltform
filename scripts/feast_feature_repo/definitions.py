# Demo 特征定义:复用 scripts/08-create-demo-data.sh 已经建好的
# iceberg.demo.orders 表(不新起一套无关的 demo 数据)。把每一笔订单当一个
# "事件":entity 是下单客户,features 是这笔订单的地区/品类/金额——用来演示
# point-in-time 正确的特征检索,不是刻意设计的最佳实践 schema。
from datetime import timedelta

from feast import Entity, FeatureView, Field
from feast.infra.offline_stores.contrib.spark_offline_store.spark_source import (
    SparkSource,
)
from feast.types import Float32, String

customer = Entity(
    name="customer_name",
    join_keys=["customer_name"],
    description="demo.orders 表里的下单客户姓名,demo 用,不是真实生产实体键设计",
)

# 用 query 而不是 table:iceberg.demo.orders 的 order_date 是 DATE 类型,
# Feast 的 timestamp_field 要求 TIMESTAMP,这里显式 CAST 一次。
orders_source = SparkSource(
    name="orders_source",
    query=(
        "SELECT customer_name, region, product, amount, "
        "CAST(order_date AS TIMESTAMP) AS event_timestamp "
        "FROM iceberg.demo.orders"
    ),
    timestamp_field="event_timestamp",
)

customer_order_features = FeatureView(
    name="customer_order_features",
    entities=[customer],
    ttl=timedelta(days=3650),  # demo 数据是固定的几笔历史订单,ttl 给大一点避免过期
    schema=[
        Field(name="region", dtype=String),
        Field(name="product", dtype=String),
        Field(name="amount", dtype=Float32),
    ],
    source=orders_source,
)
