"""The self-delete's fallback: the Compute API's instance delete, with the metadata server's token.

gcloud that found no credentials on the VM fails every call for minutes, its
own ``compute instances delete`` included, so the VM would bill until
``max_runtime`` (#434). This delete needs no gcloud. curl asks the metadata
server for the access token of the VM's attached service account, then sends
the DELETE to the Compute API's REST endpoint. Compute Engine answers with an
operation and deletes the VM, this container with it. The bench service
account's grant is the same as for gcloud's delete (ADR 0026, Decision 7).

The token reaches curl on stdin as a header line (``-H @-``), never in argv,
where any process in the container could read it. No error quotes it, nor the
metadata server's answer that holds it.
"""

from __future__ import annotations

import json
import re

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError

VM_ACCESS_TOKEN_METADATA_PATH = "instance/service-accounts/default/token"  # noqa: S105
_COMPUTE_API_URL = "https://compute.googleapis.com/compute/v1/"
# The API answers a delete with an operation at once; curl's cap sits under
# the call's, so curl reports a timeout rather than being killed.
COMPUTE_DELETE_TIMEOUT_S = 90.0
_COMPUTE_DELETE_CURL_MAX_TIME_S = 60
# One word of printable ASCII: a token holding a space, CR or LF would forge a header.
_HEADER_SAFE_TOKEN = re.compile(r"[\x21-\x7e]+")


def compute_delete_argv(project: str, zone: str, name: str) -> tuple[str, ...]:
    """curl's DELETE of the instance; its ``Authorization`` header comes on stdin."""
    url = f"{_COMPUTE_API_URL}projects/{project}/zones/{zone}/instances/{name}"
    max_time = str(_COMPUTE_DELETE_CURL_MAX_TIME_S)
    return ("curl", "-fsS", "--max-time", max_time, "-X", "DELETE", "-H", "@-", url)


def bearer_header_from(answer: str | None, what: str) -> bytes:
    """The ``Authorization`` header line for the metadata server's token answer."""
    try:
        token = json.loads(answer or "null").get("access_token")
    except (json.JSONDecodeError, AttributeError):
        token = None
    if not isinstance(token, str) or not _HEADER_SAFE_TOKEN.fullmatch(token):
        raise BenchCloudError(
            f"{what}: got no usable access_token at instance metadata "
            f"{VM_ACCESS_TOKEN_METADATA_PATH}, expected the attached service account's "
            "token (the answer is not quoted: it may hold a credential)"
        )
    return f"Authorization: Bearer {token}\n".encode()
