"""platform_sdk 的最小可跑样例——ADR-058 第一批的验证用例。

这份文件本身就是设计要证明的东西:同一份代码不用改一行,既能在本地
IDE 里跑(用 kubectl port-forward 把 config.py 的默认地址换成
localhost),也能在 JupyterHub notebook 里跑,也能被 `submit_job()`
提交成 Argo Workflow 在集群里跑。差异全部在环境变量,不在这份代码里。
"""

from platform_sdk import mlflow_setup, query

# 1. 查一下 Iceberg 里的 demo 数据,证明 Trino 连接不用手填连接串。
df = query("select * from iceberg.demo.orders limit 5")
print("查到的订单样例:")
print(df)

# 2. 往 MLflow 记一条实验数据,证明 MLflow 也是开箱即用。
mlflow = mlflow_setup("platform-sdk-hello")
with mlflow.start_run(run_name="hello-job"):
    mlflow.log_param("source", "examples/hello-job")
    mlflow.log_metric("orders_sampled", len(df))

print("完成:数据查询 + MLflow 记录都成功了。")
