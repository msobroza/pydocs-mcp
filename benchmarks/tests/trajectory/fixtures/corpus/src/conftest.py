# Presence of this root conftest makes pytest add the workspace root to
# sys.path (prepend import mode), a belt-and-suspenders companion to the
# `pythonpath` ini option so `import widgetlib` resolves however pytest is run.
