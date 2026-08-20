---
name: submit-job
description: Scaffold and submit a Python job (training run, batch script, one-off data task) to run on this platform's Argo Workflows cluster using platform_sdk, instead of the user writing raw Argo YAML or Kubernetes manifests by hand. Use this whenever the user wants to run something "on the cluster" / "on the platform" rather than just locally, wants to train a model at scale, wants to schedule or trigger a script to run outside their notebook session, or asks how to turn a notebook cell or local script into something that runs unattended. Also trigger if the user is looking at examples/hello-job or asks what job.yaml is for.
---

# Submitting a job to run on the platform

This platform runs user-submitted, one-off jobs (training runs, batch scripts) on
Argo Workflows, via the `platform_sdk` package. This is deliberately separate from
Airflow, which handles the platform's own scheduled/cron pipelines
(`seatunnel_device_events`, `feast_materialize`, `dbt_demo`) — don't confuse the two.
If the user wants a recurring scheduled pipeline rather than a one-off run, that's an
Airflow DAG, a different (and bigger) conversation than this skill covers.

Full design rationale: `docs/decisions/058-lightweight-developer-experience.md`.
Working example to copy from: `examples/hello-job/`.

## The shape of a job

Every job is a plain Python script plus a tiny declarative `job.yaml`. There is no new
DSL to learn — `job.yaml`'s fields are exactly `submit_job()`'s keyword arguments.

```
my-job/
├── job.py       # ordinary Python — no special base class, no decorators
└── job.yaml     # what to run it with
```

`job.py` should just use `platform_sdk` the same way it would in a notebook (see the
query-data skill for the data-access half). This is the whole point: the same file
works locally, in a notebook, and as a submitted job, because all environment
differences are pushed into environment variables, never into the code.

`job.yaml`:

```yaml
name: my-job          # required — must be a valid Kubernetes name (lowercase, digits, hyphens)
script: job.py         # required — path relative to this job.yaml
# image: local/platform-runtime:0.1.0   # optional, this is already the default
# cpu: 200m
# memory: 512Mi
# env:
#   SOME_VAR: some-value
```

Only `name` and `script` are required. Don't invent extra fields — the CLI rejects
anything not in this exact set (`platform_sdk/cli.py`'s `_ALLOWED_KEYS`), on purpose,
so a typo'd field fails loudly instead of silently doing nothing.

## Submitting

Either:

```bash
platform-submit job.yaml
```

or, equivalently, from Python:

```python
from platform_sdk import submit_job
workflow_name = submit_job("my-job", "job.py")
```

Both create a ConfigMap holding the script (there's a ~900KB practical limit — this is
meant for single-file scripts; a real multi-file project should be pulling from git
instead, which is a documented but not-yet-built follow-up, see ADR-058's "second
batch") and an Argo Workflow that mounts it and runs `python3 job.py`.

## Triggering an existing WorkflowTemplate instead of submitting a script

`submit_job()` is for the user's *own* ad-hoc script. If instead the user wants to
kick off a pipeline the platform has already declared as an Argo `WorkflowTemplate`
(for example, `train-demo-model`, the multi-step training pipeline defined in
`apps/argo-workflows-training-image/manifests/workflow-template.yaml`), that's a
different function — `run_workflow_template()`, not `submit_job()`. Don't scaffold a
`job.yaml`/`job.py` for something that already has a WorkflowTemplate; that would be
reimplementing what the template already does, badly.

```python
from platform_sdk import run_workflow_template
workflow_name = run_workflow_template("train-demo-model")
```

This is the "trigger training from a notebook cell" path (`docs/BACKLOG.md` P1.7) —
one line in a notebook, no `kubectl create`, no hand-written Workflow YAML. It creates
a thin `Workflow` object with a `workflowTemplateRef` pointing at the named template;
the template itself owns the image/resources/credentials, so there's nothing else to
configure unless the template declares `parameters` (pass those as a dict:
`run_workflow_template("train-demo-model", parameters={"key": "value"})` — check the
template's own `spec.arguments.parameters` for what it accepts before assuming a key
exists). The returned name works with `job_status()`/`job_logs()` exactly like
`submit_job()`'s does — see the debug-job skill.

## One real limitation to know about before troubleshooting it yourself

Calling `submit_job()` **from inside a JupyterHub notebook pod itself** currently
fails with a connection error to the Kubernetes API server — this is a known,
documented, *unresolved* NetworkPolicy issue (`docs/BACKLOG.md` section 2.6), not
something wrong with the user's code or credentials. `query()` and `mlflow_setup()`
work fine from inside a notebook; only the job-submission call is affected, because
it needs to reach the Kubernetes API server, which the notebook's network policy
doesn't currently permit.

**Workaround**: run `platform-submit job.yaml` from a terminal with a real
`~/.kube/config` (the user's own machine, or a CI runner) instead of from inside the
notebook's Python kernel. Don't spend time re-diagnosing this NetworkPolicy issue from
scratch — read `docs/BACKLOG.md` 2.6 first, it documents exactly which fixes were
already tried and ruled out.

## After submitting

See the debug-job skill for checking status, reading logs, and diagnosing failures.
