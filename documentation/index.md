---
sd_hide_title: true
---

# pydocs-mcp

```{image} _static/overview.png
:alt: pydocs-mcp architecture overview
:align: center
:width: 100%
```

<p style="text-align:center; font-size:1.25rem; margin-top:1.5rem;">
<strong>Local, version-aware code &amp; docs search for your AI coding agent —
over the exact library versions installed on your machine.</strong>
</p>

pydocs-mcp indexes your project plus every installed dependency, right on your
machine, in seconds. Your agent connects over MCP and gets answers grounded in
*your* code — fully offline. The only call that ever leaves your machine is the
optional LLM reasoning mode, and only if you turn it on with your own key.

::::{grid} 1 1 3 3
:gutter: 3

:::{grid-item-card} {octicon}`rocket` Install
:link: getting-started/install
:link-type: doc

Prebuilt wheels for Linux, macOS, and Windows — no toolchain needed.
:::

:::{grid-item-card} {octicon}`search` How it works
:link: getting-started/quickstart
:link-type: doc

Index, search (keyword + meaning + reasoning), answer over MCP.
:::

:::{grid-item-card} {octicon}`code` API reference
:link: reference/index
:link-type: doc

The public surface: exceptions, the MCP server, and application services.
:::

::::

```{include} ../README.md
:start-after: "## What you get"
:end-before: "## How it works"
:relative-docs: ..
:relative-images:
```

```{include} ../README.md
:start-after: "## How it works"
:end-before: "## Quick start"
:relative-docs: ..
:relative-images:
```

```{toctree}
:hidden:
:caption: Getting Started
getting-started/install
getting-started/quickstart
getting-started/mcp-clients
```

```{toctree}
:hidden:
:caption: User Guide
user-guide/cli
user-guide/configuration
user-guide/live-reindex
```

```{toctree}
:hidden:
:caption: Retrieval & R&D
rnd/index
rnd/pipeline
rnd/reference-graph
rnd/cache
```

```{toctree}
:hidden:
:caption: Architecture
architecture/index
architecture/patterns
architecture/specification
```

```{toctree}
:hidden:
:caption: Reference
benchmarks/index
extending
reference/index
changelog
contributing
```
