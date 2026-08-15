-- 演示 ref() 依赖链(stg_orders -> daily_order_totals),给 Cosmos 拆
-- Airflow 任务用——两个模型才能看出"逐模型可见/可重试"这个价值,一个
-- 模型看不出来。
select
    order_date,
    region,
    count(*) as order_count,
    sum(amount) as total_amount
from {{ ref('stg_orders') }}
group by order_date, region
