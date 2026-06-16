Application services
====================

The use-case services wired by the composition root
(``server.py`` / ``__main__.py`` / ``storage/factories.py``). They depend only on
the storage Protocols and take a ``uow_factory`` closure — see the
:doc:`architecture overview </architecture/index>`.

.. automodule:: pydocs_mcp.application
   :members: DocsSearch, ApiSearch, LookupService, TreeService, ReferenceService,
             IndexingService, ProjectIndexer, ModuleInspector, PackageLookup
