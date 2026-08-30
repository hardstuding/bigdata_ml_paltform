"""平台作业的公共小工具。**这份是权威源。**

`jobs/<每个作业>/jobkit.py` 是它的逐字节副本,由
`scripts/check-duplicated-sources.py` 在 CI 里保证不漂移。

**为什么是复制而不是共享一份**:作业的文件是按目录挂进容器的
(`scripts/render-jobs.py` 把每个作业目录下的 .py 放进 ConfigMap),
一个作业看不到另一个作业的文件。要真正共享得打成内部包(ADR-083)——
对几十行工具函数不值得。复制的代价(会漂移)由那个检查器兜住。

改这个文件之后跑 `python3 scripts/check-duplicated-sources.py --fix`。

---


**它存在的意义是证明多文件作业真的能用**:同目录下的 .py 会被一起挂进
容器,`import jobkit` 直接可用(PYTHONPATH 指向 /scripts)。在这之前一个
作业只能是一个文件,稍长一点就只能把所有东西堆进一个几百行的脚本。
"""
import datetime
import os


def param(name, default=None):
    """读作业参数。

    参数在 `job.yaml` 的 `params` 里声明,平台以 `PARAM_<大写名>` 注进环境
    变量。**补数就靠它** —— 没有参数,重跑一个日更作业只会再算一遍今天:

        argo submit --from cronwf/daily-order-summary -p run_date=2026-08-01
    """
    value = os.environ.get(f"PARAM_{name.upper()}", "")
    # Argo 没有替换到值时会原样留下 `{{workflow.parameters.x}}`,那不是一个
    # 有意义的值 —— 当成没传,用默认值。不这么处理的话它会被当成日期字符串
    # 一路带进 SQL,报出来的错和真正的原因差着十万八千里。
    if not value or value.startswith("{{"):
        return default
    return value


def run_date():
    """这次要处理哪一天。默认今天,可以用 run_date 参数覆盖(补数)。"""
    return param("run_date") or datetime.date.today().isoformat()


def rows_of(result):
    """`query()` 装了 pandas 返回 DataFrame、没装返回 (列名, 行)。

    **不要直接 `for r in result`** —— 在 DataFrame 上那是遍历列名,不是遍历
    行,而且不会报错,只会安静地给出错的东西。
    """
    if hasattr(result, "itertuples"):
        return [tuple(t)[1:] for t in result.itertuples()]
    return result[1]
