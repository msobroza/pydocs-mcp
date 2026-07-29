"""The optimizable assets — the harness platform's "weights".

Everything under here is TEXT an optimizer may propose changes to (or that is
deliberately frozen as an experimental control): a diff in this folder IS a
guidance change, never a code change. The loaders that read these files live in
``harness/platform/`` so this tree stays machinery-free; sub-packages exist only
because ``importlib.resources`` needs a real package at every level.
"""
