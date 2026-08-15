-- 轻量清洗层,列名/表结构和 scripts/08-create-demo-data.sh 建的
-- iceberg.demo.orders 保持一致(真实存在的表,不是编的 schema)。
select
    order_id,
    customer_name,
    region,
    product,
    amount,
    order_date
from {{ source('demo', 'orders') }}
