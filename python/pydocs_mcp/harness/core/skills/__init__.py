"""The packaged skill-artifact pool — the harness platform's "weights files".

First resident: the hand-written search-guidance seed
(``search_guidance_seed.md``), one delimited document of BACKBONE + two
TASK_HEAD + four HARNESS_TASK_HEAD sections loaded through
``harness/core/skill_artifact_loader.py``. Per spec
§8.2 decision 3 the product ships the SEED until the §6 experiment reports;
a trained revision lands only as a reviewed commit through the §4.4
promotion path. Looked up via ``importlib.resources`` so the path is correct
under wheel installs and zipimport.
"""
