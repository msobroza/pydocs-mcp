"""The supervisor's own VM: deleted after an ending it reached by itself, and after a refusal.

The VM deletes itself through the Compute API with the bench service
account, whose grant covers only instances named ``bench-…``; the instance is
always this VM's own, from the host facts. The delete goes through gcloud
first. If that fails, it is sent again without gcloud, with the token the
metadata server gives the service account (``compute_api_delete``). gcloud
that found no credentials fails every call for minutes, its delete included
(#434). A failure of both is logged: the platform's maximum run duration
still deletes the VM at ``max_runtime``.

**On a refusal.** A supervisor that refuses its run, or cannot read it, runs
nothing and writes nothing, so its VM would only bill until ``max_runtime``:
a create the launcher gave up on (recorded ``failed``) that came up anyway,
the first VM of two concurrent launches of one run, or a VM whose gcloud has
no credentials. Once the host facts are known, it deletes itself, unless the
spec copy it can read says ``self_delete: false`` (the owner wants to look at
the VM). An unreadable copy keeps the default, which deletes.
"""

from __future__ import annotations

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, BenchCloudError
from pydocs_eval.bench_vm.host_facts import HostFacts
from pydocs_eval.bench_vm.supervised_run import readable_spec_copy
from pydocs_eval.bench_vm.supervisor_events import log_supervisor_event


def delete_own_vm(cloud: BenchCloud, facts: HostFacts) -> None:
    """Ask the Compute API to delete this VM, through gcloud, then with the metadata token.

    A failure of both is logged; ``max_runtime`` still deletes the VM.
    """
    log_supervisor_event(
        "bench_vm_self_delete_requested", run_id=facts.run_id, instance=facts.instance
    )
    try:
        cloud.delete_instance(facts.project, facts.zone, facts.instance)
    except BenchCloudError as exc:
        _delete_own_vm_with_metadata_token(cloud, facts, gcloud_error=str(exc))


def _delete_own_vm_with_metadata_token(
    cloud: BenchCloud, facts: HostFacts, *, gcloud_error: str
) -> None:
    # WHY: gcloud that found no credentials fails every call for minutes, its
    # own delete included (#434); the metadata server's token needs no gcloud.
    log_supervisor_event(
        "bench_vm_self_delete_retried_with_metadata_token",
        run_id=facts.run_id,
        instance=facts.instance,
        detail=gcloud_error,
    )
    try:
        cloud.delete_instance_with_metadata_token(facts.project, facts.zone, facts.instance)
    except BenchCloudError as exc:
        log_supervisor_event(
            "bench_vm_self_delete_failed",
            run_id=facts.run_id,
            instance=facts.instance,
            detail=str(exc),
            gcloud_detail=gcloud_error,
        )


def delete_refused_vm(cloud: BenchCloud, facts: HostFacts) -> None:
    """After a refusal: delete this VM, unless the readable spec copy turns ``self_delete`` off."""
    spec = readable_spec_copy(cloud, facts)
    if spec is not None and not spec.self_delete:
        log_supervisor_event(
            "bench_vm_refused_vm_kept",
            run_id=facts.run_id,
            instance=facts.instance,
            detail="the spec copy says self_delete: false; max_runtime still deletes it",
        )
        return
    delete_own_vm(cloud, facts)
