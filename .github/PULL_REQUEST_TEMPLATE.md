<!--
The pull request title becomes the commit subject when this is squash-merged.
Write it as a Conventional Commit: type(scope): summary
  types:  feat, fix, docs, test, refactor, build, ci, chore
  scopes: core, introspect, capture, server, cli, web, demos, docs, ci
-->

## What and why

<!-- What does this change, and what problem does it solve? Link the issue: "Closes #123". -->

## How it was checked

<!-- The commands you ran, or what you looked at in the viewer. A screenshot helps for UI changes. -->

## Checklist

- [ ] The checks for the areas I touched pass (see `CONTRIBUTING.md`).
- [ ] Tests cover the change, and they run offline with `TestModel` or `FunctionModel`.
- [ ] Docs are updated if behaviour, a signature or a CLI flag changed.
- [ ] `CHANGELOG.md` has a line under "Unreleased" if a user would notice the change.
- [ ] If the wire format changed: `schema/pviz.schema.json` is regenerated, and `web/src/wire.ts`, the fixtures and the wire format page match.
- [ ] No recording or export of a real run is included.
