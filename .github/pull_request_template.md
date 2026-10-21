## What and why

<!-- What changes for someone using asktrace, and why. Link an issue if there is one. -->

## How it was checked

- [ ] `uv run ruff format --check . && uv run ruff check . && uv run pytest -q`
- [ ] Ran the changed commands locally (say which, and on sample or real data)

## Privacy and compatibility

- [ ] Shareable outputs (grass SVG, README examples, fixtures, screenshots) contain no question
      text, concept names, reflections or personal paths
- [ ] An existing database still opens and keeps its questions, tags and reflections; say so if
      a re-ingest or migration is needed
- [ ] `asktrace hook` still exits 0 and prints nothing on stdout
- [ ] Not applicable to this change: <!-- list items above that do not apply -->

## Notes for reviewers

<!-- Decisions made, known gaps, follow-ups. -->
