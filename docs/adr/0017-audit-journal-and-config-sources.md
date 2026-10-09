# ADR-017: Audit Journal and Configuration Sources

**Status:** Accepted
**Date:** 2026-10-01
**Amended:** 2026-10-04 — the journal in every process that writes (ADR-023)
**Amended:** 2026-10-08 — its number in its title reads `ADR-017`, as the index does (correction)
**Amended:** 2026-10-08 — a server with a read-only `HOME` needs `GORA_AUDIT_LOG` too (correction)
**Amended:** 2026-10-09 — the journal is a stream where nobody runs a command (ADR-027)

---

## Context

Two defects, one session, one cause: a boundary of the process that nobody had decided.

**The audit journal did not exist.** ADR-002 and ADR-004 require every mutating operation to
be auditable, and `AuditLogger` calls `record()` on every one of them. But `record()` writes to
the logger `gora.audit`, and nothing in `src/` attaches a handler to it (ADR-016, last row of
its table). In the CLI, zero events reached anything. "Auditable" was being read as "`record()`
is called". It means the event reached a sink, and that is what this ADR makes true.

**`Settings` read a file from the working directory.** `model_config` carried
`env_file=".env"`, so what a command did depended on the directory it was started in. That is
the root cause of the 2026-10-01 Slack incident. ADR-016 records the same mechanism measured
from the other side: a `.env` with Slack enabled produced 16 connections to `slack.com` from
integration tests, and no test failed.

Out of scope here, and not decided: trust and digest verification.

## Decision

### Settings read the process environment only

`Settings` has no `env_file`. No other default file is added: no file in `$XDG_CONFIG_HOME`, no
file beside the manifest. The one thing that loads a file into a process is the human-only
`gora-dogfood` function in the workspace, which sets variables in a subshell. Whoever starts the
process sets its secrets; GORA never goes looking for them. `.env.example` is deleted and the
README documents every `GORA_*` name, its meaning and its default, without values.
*Enforcement class:* `test` (a `.env` in the working directory is ignored, shown in a child
process, because the conftest guard neutralises `env_file` in the pytest process).

### The journal has a path

```
GORA_AUDIT_LOG  >  $XDG_STATE_HOME/gora/audit.jsonl  >  $HOME/.local/state/gora/audit.jsonl
```

- `XDG_STATE_HOME` empty or relative is ignored, as the XDG Base Directory spec says.
- `GORA_AUDIT_LOG` empty counts as unset. Relative is a usage error: exit 2, nothing written.

  *Amended 2026-10-09 (ADR-027 section 3):* `-` is the stream, standard error, never a path.
- With no override, no usable `XDG_STATE_HOME` and a `HOME` that is not an absolute path, there
  is no default: exit 2, and the message says to set `GORA_AUDIT_LOG`. GORA does not ask the
  password database where `HOME` would be.
- The path is resolved by hand. `platformdirs` is not used.

*Enforcement class:* `test`.

### The sink

- **Attached in the CLI only, lazily, for mutating commands:** `cut-rc`, `promote`, `rollback`
  and `promote --open-pr`. Not at import time, and not in the API process, which is read-only
  (ADR-014). `gora version` and `gora status` do not create the directory. *Class:* `test`.

  *Amended 2026-10-04 (ADR-023 sections 4 and 7):* the API writes too, so the journal is
  attached in every process that writes, the server included, and records the code-host
  account that asked and the identity that acted. Where a pipeline run's journal is kept beyond
  the run is decided with the server; until then GORA's comment on its pull request is the
  durable record of who asked.

  *Amended 2026-10-09 (ADR-027 section 3):* decided. A process that runs without a person, the
  release repository's pipeline and GORA's server, writes its journal to standard error, one
  event per line in the format above, selected by `GORA_AUDIT_LOG=-`; the platform that runs it
  keeps it as it keeps every service's logs. Standard output stays the command's result. For the
  stream, "Files" and the torn-line rules do not apply: `-` is file descriptor 2, with no path,
  no regular-file check, no `fsync` and no last byte to check; each event is one write under one
  lock in the process, and a reader keeps only the lines that parse as events. The failure
  policy applies. Who asked and who approved stay on the pull request.
- **A dedicated handler on `gora.audit`, with `propagate=False`.** `Journal` is not a stock
  `logging.FileHandler`: it catches a failed write itself and keeps the event in
  `unrecorded`, and the CLI inspects that before it exits. The logger is set to INFO while the
  sink is attached, because it otherwise inherits WARNING and drops every event. *Class:* `test`.
- **Files.** Missing directories are created `0700`, each one explicitly, since `mkdir` honours
  the umask and `makedirs` leaves its parents at the umask. The file is opened
  `O_WRONLY|O_APPEND|O_CREAT`, mode `0600`, and tightened if it was wider. For the default
  locations the `gora` directory is tightened to `0700` if it was wider. A directory chosen
  with `GORA_AUDIT_LOG` is the operator's: missing parts are created `0700`, an existing one is
  left as it is. Anything that is not a regular file is refused: `/dev/null` would swallow
  every event and still look open. The open uses `O_NONBLOCK`, which changes nothing for a
  regular file but makes a FIFO with no reader fail at once (exit 1) instead of blocking the
  command forever; a FIFO with a reader reaches the not-a-regular-file refusal. *Class:* `test`.
- **One write per event**, newline-terminated JSON, then `fsync`. A short write is completed;
  a write that makes no progress is a failure. Event lines are printable ASCII (`json.dumps`
  escapes everything but DEL, which `AuditLogger` escapes too), so a line can go to a file or
  a terminal as it is. *Class:* `test`.
- **A torn line never merges with the next event.** A write can land part of a line and then
  fail. Two rules keep that fragment from swallowing an event:
  1. *In a command:* after any failed write or failed `fsync` the journal is broken for the
     rest of that command. Every later event goes to `unrecorded` and nothing more is
     written, because the tail of the file is no longer known. Without this, the next event
     would be appended straight after the fragment and both would be lost as one unparseable
     line, while the command reported the second as recorded.
  2. *Across commands:* when the journal is opened and the file is non-empty and its last
     byte is not a newline (a previous command died mid-line), the first event written
     starts with a newline. Existing bytes are never truncated or rewritten. A file that
     cannot be read to check counts as torn: a blank line is harmless, a merged event is not.
  *Class:* `test`.

### Failure policy

1. **Before any mutation, the journal is opened, and the same descriptor is held for the whole
   command.** It is never closed and reopened. If it cannot be opened: exit 1, a message on
   stderr, the manifest byte-identical, no remote call made. This precedes every other step of
   a mutating command, including the validation of its own arguments beyond what the parser
   does. *Class:* `test`.
2. **A write that fails after a mutation does not raise into a service or an adapter and does
   not abort the sequence.** `promote --open-pr` is up to four events; stopping after the
   third leaves a branch without a pull request. The operation completes. Then, on stderr, a
   line says the operation completed and that N events were not recorded in the audit journal,
   followed by every one of those events in full, one JSON line each, in order. Exit 3. Once a
   write has failed, every later event of the command is reported this way too (see the torn
   line rule above). *Class:* `test`.
3. **When the operation itself failed** (exit 1) and some of its events missed the journal,
   the same lines are printed, the statement says the operation did not complete, and the exit
   code stays 1. Exit 3 means "completed, audit incomplete" and nothing else.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | ok |
| 1 | refused or failed, including a journal that cannot be opened |
| 2 | usage, including a journal location that cannot be resolved |
| 3 | completed with an incomplete audit |

### No secret in an event

Two guards, both shown red before they were trusted (ADR-016 rule 1):

- **by name:** every event type found in `src/` (the scan reads the source, so a new type fails
  until it is sampled) is produced by a real run, and no field name at any depth may match
  `token|secret|password|key`, case-insensitively;
- **by value:** every `GORA_*TOKEN` and `GORA_*SECRET` Settings reads is set to a sentinel,
  every mutating command is run against fake GitHub and Slack, and no sentinel may appear in
  the journal, in the output, or in the events printed when the journal missed them.

*Class:* `test`.

### No rotation

Decided, not forgotten. The journal is appended to by a human running a command, a handful of
lines a day. **Falsifier:** the day anything automatic appends to it (a scheduler, a daemon, a
command triggered from Slack or from the API), rotation becomes a real task and this section is
amended. *Class:* `review-only`.

*Amended 2026-10-09 (ADR-027 section 3):* automatic writers use the stream, so the file is still
appended to only by a person, and this section holds; the falsifier reads "the day anything
automatic appends to the file".

### Tests never touch the real home

The conftest fixture points `HOME`, `XDG_STATE_HOME`, `XDG_CONFIG_HOME` and `GORA_AUDIT_LOG`
under each test's `tmp_path`, in the test's process and, through `os.environ`, in every child
(TESTING rule 6). `tests/conftest.py` is gate-defining (ADR-016 rule 7). *Class:* `test`.

## Why not otherwise

- **`platformdirs`.** On macOS it returns `~/Library/Application Support`, a place nobody
  looks for a log and that the system backs up and syncs. The XDG state directory is the same
  on both platforms GORA runs on, and it is one line of code.
- **`~/.config` for the journal.** State is not config. Config is linked from a repository and
  reproducible: delete it and it comes back. The journal is created by the application, cannot
  be regenerated, and must not be linked into a repository or swept by something that treats
  `~/.config` as disposable.
- **A default config file read by `Settings`.** It reopens the incident. A file that is read
  because it exists is a file that can switch a run to live integrations without the person
  running it having typed anything. The only file loader is the one a human invokes on purpose.
- **A stock logging handler.** `logging.FileHandler` hands a failed write to `handleError`,
  which prints to stderr and returns. The caller is never told, the command exits 0, and the
  event is gone. A test shows this, and the exit-3 test fails if the stock handler is swapped
  in.
- **Aborting the sequence on a late write failure.** The mutation has already happened. An
  abort cannot undo a branch created on GitHub; it can only leave it without its pull request.
  Completing the operation and printing what was not recorded loses no event: each one that
  missed the journal is on stderr in full. It does not leave the file clean. A write that
  landed part of an event leaves a torn line in the journal, which does not parse; the rules
  above keep it from swallowing the next event, and the event itself is on stderr.
- **Rotation now.** Nothing writes to the journal faster than a person types. Building it now
  is code, tests and a failure mode for a problem the journal does not have yet. The falsifier
  above says when that changes.

## Consequences

- A mutating command writes to the journal or says, loudly and with the events in full, that
  it did not. A green run now means the event reached a sink.
- **A container with a read-only `HOME` needs `GORA_AUDIT_LOG` for mutating commands**, set to
  a path on a writable volume. Without it the command stops before it changes anything: exit 1.
  Read-only commands and the API are unaffected.

  *Amended 2026-10-08 (correction, ADR-023 section 7):* the API writes too (the note of
  2026-10-04 above), so a server whose `HOME` is read-only needs `GORA_AUDIT_LOG` as well.

  *Amended 2026-10-09 (ADR-027 section 3):* a server uses the stream (`GORA_AUDIT_LOG=-`), which
  needs no writable `HOME`.
- A journal can contain a line that does not parse: the torn fragment of an event whose write
  failed. A reader skips lines that are not JSON; the event they belonged to was printed on
  stderr when it happened.
- Each event costs an `fsync`. It is acceptable at the rate a person runs commands, and it is
  what makes "reached the sink" mean something.
- The journal is a record, not a tamper-evident log: there is no hash chain and no signature.
  Whoever can write the file can edit it. That is outside this decision.
- Concurrent commands append safely (`O_APPEND`, one write per line), but each holds its own
  descriptor, so lines of different commands interleave in the order they were written.
- A command's own arguments are validated by the parser first; after that the journal is the
  first thing it opens. A refused command (for example a missing candidate) therefore creates
  an empty journal when none existed.
- Anything in the environment that used to come from a `.env` must now be exported by the
  process that starts GORA.

## Amendments

### 2026-10-04 — the journal in every process that writes

ADR-023 makes the HTTP API a writer and records who asked for each write. "The sink" is amended
in place: the journal is attached in every process that writes, the server included.

### 2026-10-08 — its number in its title

The title read `ADR-0017`; it reads `ADR-017`, the form every other ADR and the index use.

### 2026-10-08 — a server with a read-only `HOME` needs `GORA_AUDIT_LOG` too

A correction: Consequences said the API is unaffected by a read-only `HOME`; since the API
writes (2026-10-04), it needs a writable journal like any process that writes.

### 2026-10-09 — the journal is a stream where nobody runs a command

ADR-027 decides where a pipeline run's and the server's journal is kept. For this ADR: "The journal
has a path" reads `-` as the stream, never a path; "The sink" gains the stream, standard error, to
which "Files" and the torn-line rules do not apply; "No rotation" holds, since only a person appends
to the file; a server needs no writable `HOME` for its journal.
