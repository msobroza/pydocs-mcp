# Authoring prompt — the `example-needle-chat` held-out questions

This is the complete instruction the authoring agents received when they drafted
the held-out questions (`test` and `reserved`) of `records.jsonl` and wrote their
gold. Nothing else was in their prompt. It names the question shapes and the
pinned checkout only: it carries no text from the chat agent's prompts and no
earlier agent answer, trace or question, so the held-out set does not follow
the wording of what it measures.

The checkout directory and the two output paths in braces were filled in
at dispatch time.

## The corpus

- Repository: `https://github.com/msobroza/example_needle`, commit
  `9c170b02fe93a759ddf5739b777e07e3c162b12b`, checked out at `{checkout_dir}`.
- Read files under `{checkout_dir}` only. Open no other directory, and use no
  knowledge of this repository from anywhere else.
- The corpus is every file with one of these extensions: `.py .md .toml .yaml
  .yml .cfg .ini .txt .json .rst`. Files under `tests/` are part of the corpus
  but are never gold.

## The question shapes

A shape says what a finished answer looks like.

- `where`: one place answers it ("Where is X decided?"). A finished answer names
  the file, the symbol and the lines.
- `where_all`: several places answer it together ("Which places ...?", "What
  are all the ...?"). A finished answer names every one of them.
- `how_flow`: a sequence across functions or files ("What happens, step by step,
  when ...?"). A finished answer walks the chain in order and names each step's
  symbol and file.
- `why`: a design reason that the code or the docs make visible ("Why does X
  ...?", "How does X avoid ...?"). A finished answer names the mechanism and
  where it lives, and cites the doc that states the reason when one does.
- `how_to`: a task a user of the library performs ("How do I ...?"). A finished
  answer gives the calls or commands in order, and where each one is defined.
- `compare`: two or more alternatives ("What is the difference between A and B,
  and when would I pick each?"). A finished answer names each alternative and
  the difference, and cites each one's definition.
- `vague`: an under-specified question ("How does X work?"). A finished answer
  picks the reasonable reading, answers it, and cites the places it relied on.

## Part 1 — draft the questions

Draft 40 candidate questions: 5 `where`, 8 `where_all`, 7 `how_flow`, 5 `why`,
5 `how_to`, 5 `compare`, 5 `vague`. More are drafted than will be kept.

- Write the way a developer new to this repository asks in a chat: plain
  language, no file paths, no line numbers. Name a symbol only when a developer
  would naturally know its name.
- Every question is answerable from the checkout alone, and its answer lives in
  code or docs, never only in tests.
- Spread the questions across the whole repository: the `needle`,
  `needle_core` and `needle_demo` packages, the scripts, the examples, the docs
  and the configuration files. No two candidates share their main answer
  location.
- For the shapes whose answer spans several places (`where_all`, `how_flow`,
  `how_to`, `compare`, `vague`), prefer questions whose full answer needs at
  least two files.
- Avoid questions that a single sentence of the README answers.

Write a JSON array to `{candidates_path}`, one object per candidate:

```json
{"candidate": "c01", "shape": "where_all", "question": "...",
 "answer_sketch": "one or two sentences: what the answer is and where it lives",
 "answer_files": ["src/...", "docs/..."]}
```

## Part 2 — write the gold for a question

For each question you are given:

1. Work out which identifiers the answer depends on: function, class, method
   and constant names, configuration keys, command names.
2. Grep the checkout for each identifier fresh (for example
   `grep -rn --include='*.py' --include='*.md' ... '<identifier>' {checkout_dir}`
   over every corpus extension).
3. Account for every hit outside `tests/`: it is either a gold site, or it is
   excluded with a one-line reason (a re-export, an unrelated symbol with the
   same name, a changelog line, a mention that adds nothing to the answer).
4. A gold site is `path` (relative to the checkout root), `start` and `end`
   (1-indexed, inclusive) and `symbol`. The span covers the smallest complete
   unit that answers: a whole function, method or class, a docs section, a
   configuration block. `symbol` is one identifier — letters, digits,
   underscores and dots, starting with a letter or an underscore — that
   appears inside the span and names what the site is about.
5. Put the site that answers most directly first, then the others in the order
   a finished answer would present them. A test file is never a gold site.

Write a JSON array to `{gold_path}`, one object per question:

```json
{"candidate": "c01",
 "sites": [{"path": "src/...", "start": 10, "end": 42, "symbol": "Name",
            "why": "one line: what this site contributes to the answer"}],
 "grep": [{"identifier": "Name",
           "hits": [{"path": "src/...", "line": 12, "accounted": "site 0"},
                    {"path": "docs/...", "line": 7,
                     "accounted": "excluded: a passing mention"}]}]}
```
