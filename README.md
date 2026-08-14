# FLExText Concatenator

A cross-platform desktop app for combining SIL FieldWorks (FLEx) interlinear
`.flextext` exports. Load a folder of texts, arrange them in any order, and write
them out either as a **corpus** of separate texts or as a **single combined text**.

---

## The two modes

### Corpus — individual texts, one file

Every source text stays its own text in FLEx. The output is one `<document>`
containing one `<interlinear-text>` per source, each keeping its own title,
`<languages>`, and `<media-files>`.

**Nothing is discarded.** Audio time offsets, `media-file` links, speaker names, and
media lists all survive exactly as they were.

### Combined Text — one text in FLEx

All the source texts become a single FLEx text under a title you supply. Each source
text becomes one `<paragraph>`; every line from that text becomes a `<phrase>` inside
it. Source texts that already have several paragraphs are flattened into one.

Options: a required title, each source title as a note on its paragraph's first
phrase, what to do with `segnum`, stripping audio-timestamp note items, and how to
handle audio segmentation.

#### Audio segmentation in combined mode

**Discard** (default) removes phrase time offsets, `media-file` links, speaker names
and the `<media-files>` list. Offsets measured against separate recordings mean
nothing once the texts are merged. The app warns before doing this, and only when the
loaded files actually contain timing.

**Shift onto one concatenated recording** instead moves each text's offsets forward by
the total length of the texts before it, plus a gap — so the result lines up with the
source audio joined end to end, and the combined text can be paired with the combined
audio in ELAN. The gap defaults to **1005 ms**, exactly what the companion
[Audio Concatenator](https://github.com/rulingAnts/audio_concat) inserts between files
(500 ms silence + 5 ms click + 500 ms silence).

> ⚠️ **Durations are estimated.** A `.flextext` file does not record how long its
> recording is, so the app uses the end of the last annotation. That is exact for a
> text annotated continuously from zero, but *not* for ELAN-style annotation, which
> marks utterances and leaves silence unannotated — any audio after the final
> utterance is invisible, and every later text drifts early by that much,
> cumulatively. The app names the texts affected.

Your source files are never modified in either case, so you can always re-run.

#### Line numbers (segnum)

FLEx **ignores `segnum` on import** — its importer has an explicit no-op for it,
because there is no field on a segment to store it in. So the default is to **remove**
them and let FLEx number the text itself; *Renumber continuously* and *Keep* exist for
other tools that do read the field. `reference-label`, which FLEx *does* store, is
never touched.

---

## Features

- Load a folder of `.flextext` files, optionally including subfolders
- Add individual files, drag files in from the file manager, remove or clear
- Drag-and-drop reordering with multi-select (Shift+Click, Ctrl/Cmd+Click)
- **Simple sort** — by filename, numerical order, text title, or date
  created/modified/accessed
- **Suffix Order** — sub-sort files sharing a base name by an ordered pattern list
- **Advanced (Regex) sort** — multi-layer regex sort with configurable capture groups,
  sort-as modes (natural text, numeric, alphabetical), and per-layer direction
- Save and load all settings as commented YAML
- Non-blocking merge on a background thread, with progress and cancel
- A malformed file is skipped and reported rather than aborting the whole run
- Works as a plain Python script or as a portable app (Windows, macOS, Linux)

---

## Requirements

- Python 3.10+
- [PySide6](https://pypi.org/project/PySide6/)
- [PyYAML](https://pypi.org/project/PyYAML/) — optional, for saving/loading settings

```
pip install PySide6 PyYAML
```

No external binaries are needed.

---

## Running

```bash
python app.py
```

---

## Building a portable app (PyInstaller)

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name flextext-concat app.py
```

---

## Project structure

```
app.py            Main GUI (PySide6)
combiner.py       Background merge worker
flextext.py       FLExText parsing and merge engine (no Qt — importable on its own)
tests/            pytest suite for the merge engine
docs/             GitHub Pages documentation site
```

`flextext.py` has no Qt dependency and can be used as a library or from a script.

---

## Format notes

The output targets the FlexText interlinear format as documented in SIL's
*Technical Notes on FLEx Text Interlinear* and defined by `FlexInterlinear.xsd`.
Import into FLEx via **Texts & Words → File → Import → FLEx Text Interlinear**.

Writing systems must already exist in the target FLEx project — a `.flextext` import
will not create them, and that is the most common cause of a text importing blank.

---

## License

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE).  
Copyright 2026 Seth Johnston.

Built with assistance from [Claude](https://claude.ai) by Anthropic.
