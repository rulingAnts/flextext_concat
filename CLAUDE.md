# flextext_concat — Claude Code notes

FLExText Concatenator: a PySide6 desktop app that combines SIL FieldWorks (FLEx)
interlinear `.flextext` XML exports two ways — as a **Corpus** (many separate texts
in one file) or as a **Combined Text** (one FLEx text, one `<paragraph>` per source).

Adapted from the sibling repo `audio_concat`, which supplies the UI, the file-list
handling, the sort engine, the YAML settings round-trip, and the CI workflow.

## ⚠️ GitHub costs — ask before anything billable (firm policy, 2026-07-07)

**Claude: never trigger anything that can incur GitHub charges without Seth's explicit
approval AND a stated cost estimate first.**

- FREE, always: Actions on **public** repos with **standard** GitHub-hosted runners;
  self-hosted runners; GitHub Pages.
- METERED (free monthly quota, then paid): Actions in **private** repos (2,000 min/mo;
  **Windows counts 2×, macOS 10×**); Codespaces; Packages; Git LFS.
- **ALWAYS billable, even on public repos: larger / GPU runners** (anything beyond the
  standard `ubuntu-latest` / `windows-latest` / `macos-latest` tiers).
- Safety valve: with **no payment method on file, GitHub blocks usage at the quota and
  cannot bill** — keep it that way, or set stop-usage budgets.

So WITHOUT Seth's explicit OK (and cost), do **not**: add or change `.github/workflows/**`;
use a non-standard `runs-on:`; add a `schedule:` (cron) trigger; create Codespaces; use
Git LFS; publish private Packages; or change the plan / budgets. The local
`.git/hooks/pre-push` blocks workflow pushes (override `ALLOW_WORKFLOW_PUSH=1`) and
production-branch pushes (`ALLOW_MAIN_PUSH=1`) — set those flags only after Seth approves
that specific push.

This repo is **public** and uses only standard runners, so its `build.yml` costs $0.
Seth approved that workflow on 2026-08-14; the approval covers that workflow only.

## Git workflow

`main` is production. All work happens on `dev`; never merge or push to `main` until
Seth has tested and explicitly approved a release.

## FLExText format — the constraints that matter

Verified against SIL's spec (Ken Zook, *Technical Notes on FLEx Text Interlinear*,
2026-05-04), the normative `FlexInterlinear.xsd`, and FieldWorks'
`InterlinearExporter.cs` / `BIRDInterlinearImporter.cs`.

- One `<document>` holds **N `<interlinear-text>` siblings**. `<languages>` lives
  **inside each** `<interlinear-text>` (after `<paragraphs>`), never at document level —
  the exporter resets `m_usedWritingSystems` per text, so each text legitimately lists
  only its own writing systems. `<media-files>` is likewise per-text.
- `phrase/@media-file` is an IDREF to `media/@guid` **scoped to the enclosing text**, so
  it survives concatenation untouched. Corpus mode needs no guid rewriting.
- **Phrase children run `item* words item*`** — items appear on *both* sides of
  `<words>` (real files show `txt, segnum, words, gls, note`). Never rebuild a phrase by
  gathering items then words; that silently reorders data. Only remove or append.
- **`<words>` is optional in practice** despite `minOccurs="1"` in the XSD (ELAN omits
  it). Be permissive on input, strict on output.
- **`<paragraph>` cannot carry `<item>` children** (schema: `xs:all` with exactly one
  `<phrases>`). Source titles therefore attach to a *phrase*, not a paragraph.
- **guids are hints, not keys.** `interlinear-text/@guid` only drives duplicate
  detection on import; stripping it makes FLEx create a new text silently instead of
  firing N confusing "Duplicate text found" prompts (LT-22167).
- **Preserve `vernacular="true"`.** Without it `BIRDInterlinearImporter` falls back to
  `SetVernacularLanguagesByUsage()` and guesses which writing system is vernacular.
- `document/@version`: `2` is standard; `3` (FLEx 9.3.5+) adds `<run>` children for
  embedded writing systems/styles. Emit `3` if any input is v3 or contains `<run>`.
- **`segnum` is write-only.** `BIRDInterlinearImporter.AddSegmentItemData` has an
  explicit `case "segnum": break;` — "not associated to a property, and also not a
  custom field". It is written on export from the reference FLEx computes for the
  screen, so nothing you put there survives import. `reference-label` →
  `Segment.Reference` is the one that *is* stored; never strip or rewrite it.
  Verified 2026-08-14 against FieldWorks `main`.
- Known false positives when validating real files against the XSD: FLEx writes
  `language/@RightToLeft` (undeclared in the schema) and ELAN omits `<words>`.
- **A `.flextext` file never records its recording's duration.** Combined mode's
  offset-shifting estimates it from the last annotation, which is exact only when
  the annotation tiles the recording contiguously from zero (the flextext.app
  style). ELAN-style annotation marks utterances and leaves gaps, so trailing audio
  is invisible and later texts drift cumulatively. Always surface which texts are
  estimates rather than presenting shifted output as exact.
- Seth's own corpus mixes three producer dialects (flextext.app, FLEx/xLingPaper,
  ELAN-annotated) differing in indentation, attribute order, and whether `<media-files>`
  precedes or follows `<languages>`. Output is normalised to FLEx's ordering.

## Open items

- **Qt pruning: done here, still pending in `audio_concat`.** The v0.1.0 macOS
  bundle was 506 MB unpacked because CI's `PySide6` metapackage installs
  PySide6-Addons; requirements now use `PySide6-Essentials`, CI passes
  `--exclude-module` for every unused Qt module, and every platform build runs
  `--smoke-test` (launches the real bundle) before packaging, so over-excluding
  fails in CI instead of on a user's machine. Apply the same treatment to
  `audio_concat`.
- **`<media location=…>` convention is unsettled.** Seth wants to decide this
  himself — do not pick one for him. Combined mode's shifted-offset output writes
  whatever the user supplies. His corpus already mixes remote
  `connect.flextext.app` URLs, absolute `file:///` URIs, and bare relative
  filenames; only 1 of 6 resolved locally when checked. Same question applies to
  FlexText Editor.
- **Audio timeline invariants** (combined + shift mode). Breaking any of these
  silently desyncs ELAN: a text with a matched recording must advance the
  timeline by that recording's *measured* length; a text absent from the joined
  audio must neither advance it nor keep offsets; and the gap used for offsets
  must be the separator's *rendered* length, not its nominal one. Corpus mode
  does none of this — it is a verbatim passthrough.
- **pydub is on borrowed time.** It imports the stdlib `audioop`, which PEP 594
  removed in Python 3.13, and pydub is effectively unmaintained. CI pins 3.12
  for this reason; bumping it builds fine and then fails at runtime on any
  audio operation. Replacing pydub (or vendoring an `audioop` shim) is the real
  fix. Only `audio.py` touches it.
- **Not yet verified by a human.** No one has run the GUI by hand, and no output
  has been imported into FLEx — the one check that matters most, since FLEx's
  import error reporting is poor.

## Sample data

Read-only reference corpus (**never write to it**):
`~/Library/Mobile Documents/com~apple~CloudDocs/Fayu Linguistic Research/Interlinear Text Processing`
