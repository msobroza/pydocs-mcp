"""What gcloud's stderr says when a call answered "no", recognised by its documented markers.

gcloud reports "not found", "no objects matched" and "precondition failed"
only in its stderr; every ``GcloudBenchCloud`` call that tells those answers
apart from a failure reads them here, so the patterns cannot drift apart.
"""

from __future__ import annotations

import re

NOT_FOUND = re.compile(r"(?i)not.?found|matched no objects|no urls matched|\b404\b")
NO_OBJECTS_MATCHED = re.compile(r"(?i)matched no objects|no urls matched")
# "At least one of the pre-conditions you specified did not hold." (HTTP 412).
PRECONDITION_FAILED = re.compile(r"(?i)pre-?conditions?\b.*(fail|not hold)|conditionNotMet|\b412\b")
