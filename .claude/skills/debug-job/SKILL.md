---
name: debug-job
description: Diagnose why a job submitted via platform_sdk.submit_job() (or `platform-submit`) failed, is stuck, or produced unexpected results — including Argo Workflow status of Error/Failed, jobs stuck Pending, missing logs, or output that doesn't match expectations. Use this whenever the user reports a submitted job failed, isn't finishing, or asks "why did my workflow fail" / "how do I see the logs" / "what does Error vs Failed mean here". Also trigger if the user is looking at a workflow name like `<job-name>-xxxxx` and doesn't know what to do with it.
---

# Debugging a submitted job

Companion to the submit-job skill — read that first if the user hasn't successfully
submitted anything yet. This skill is for after `submit_job()` (or `platform-submit`)
has returned a workflow name and something's wrong.

## First: check status and logs, don't guess

```python
from platform_sdk import job_status, job_logs

print(job_status("my-job-abc12"))   # Pending / Running / Succeeded / Failed / Error
print(job_logs("my-job-abc12"))     # main container logs from every pod in the workflow
```

`job_status()` never returns an empty string — if Argo hasn't written a status yet it
reports `"Pending"`, so a blank/None result means something else is wrong (wrong
workflow name, wrong namespace, no cluster access), not "job hasn't started".

If `job_logs()` says "还没有 pod,作业可能刚提交" (no pod yet, might have just been
submitted), the workflow controller hasn't scheduled it — wait a few seconds and
retry before assuming something's broken.

## Reading the failure

**`Error` vs `Failed`** — these mean different things in Argo, don't conflate them:
- `Failed`: the container ran and exited non-zero. The Python traceback is in
  `job_logs()`. This is almost always a bug or misconfiguration in `job.py` itself, or
  a missing environment variable / credential — check the traceback first.
- `Error`: something went wrong *outside* the container — couldn't schedule the pod,
  couldn't create supporting resources, RBAC denied, etc. `job_logs()` may show
  nothing useful; check `kubectl describe workflow <name> -n argo-workflows` instead.

## Common causes, roughly in order of likelihood

1. **`MissingCredential` in the traceback** — the job needs `PLATFORM_TRINO_USER` /
   `PLATFORM_TRINO_PASSWORD` (or similar) and they weren't passed. Fix by passing
   `env={...}` to `submit_job()` or adding them under `env:` in `job.yaml`. This is
   *expected* behavior, not a bug — see the query-data skill for why there's no
   default account.

2. **`ImagePullBackOff` / `ErrImagePull`** — almost always means someone built a new
   version of `local/platform-runtime` and it isn't on this node yet, or a custom
   `image:` was specified that doesn't exist locally. Custom/local images must use
   `imagePullPolicy: IfNotPresent` (already the default `submit_job()` sets) — if this
   still happens, the image genuinely isn't built on this node; see
   `apps/platform-image/Dockerfile` for how to build it.

3. **Workflow shows `Error` immediately with a `workflowtaskresults` permission
   message** — the `serviceAccountName` on the job doesn't have the RBAC to write
   workflow results. `submit_job()` defaults to `argo-workflow`, which already has
   this permission (`apps/argo-workflows-training-image/manifests/
   workflow-serviceaccount.yaml`) — this only happens if someone overrode
   `service_account` to something else without granting equivalent RBAC.

4. **`submit_job()` itself raised a connection error before a workflow was even
   created** — that's not a job failure, that's the known NetworkPolicy limitation
   documented in the submit-job skill (calling it from inside a notebook pod). Check
   whether the caller was running inside JupyterHub before debugging further.

5. **Job ran and "succeeded" but the output looks wrong** — this isn't a
   `platform_sdk` problem, it's the job's own logic; read `job_logs()` for whatever
   the script printed and debug it like any other Python bug. Don't assume the
   platform did something to the data.

## If `job_logs()` isn't enough

Fall back to `kubectl` directly — `platform_sdk` deliberately doesn't wrap every Argo
capability, so some things need the underlying tools:

```bash
kubectl get workflow <name> -n argo-workflows -o yaml
kubectl get pods -n argo-workflows -l workflows.argoproj.io/workflow=<name>
kubectl describe pod <pod-name> -n argo-workflows   # events, not just logs
```

`kubectl describe pod` is the right tool when the failure happened before the
container even started (scheduling, volume mounts, RBAC) — `job_logs()` only shows
container output, which doesn't exist yet in those cases.
