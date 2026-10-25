# CoS-owned Apple reminder creation

MODERNIZE the existing authenticated conversation/request-action contract and
CoS selected-source reader. A fresh private iMessage, trusted CoS Gmail message,
or authenticated dashboard chat may explicitly request one timed reminder in
the CoS work list. The requester owns the private command receipt; CoS owns the
native account/source/list and performs the effect. Member Apple bridge paths
and permissions remain separate.

`administrateme_confirm_cos_apple_reminder` binds the exact source selection and
writer policy into an immutable intent. The pinned grounding skill, domain rule
`current-human-request.cos-reminder-create.v1`, and independent native OpenClaw
rule each require the same current authored instruction. Both decision receipts
remain distinct from a human allow-once. A durable attempt precedes the native
save; same-store read-back checks native ID, list, title, notes, completion state
and the exact UTC due components. A possible effect without a valid terminal is
unknown and is never automatically retried. Replays preserve original expiry.

The v1 reader app keeps its identity, bytes and existing permission. Install a
separate immutable **CoS Reminders Writer** component from the protected release:

```sh
python -m bootstrap.cos_reminders_helper_app --source "$protected_source" \
  --expected-commit "$protected_commit" --destination "$writer_app" --enable-writes
python -m bootstrap.cos_reminders_write --inspect \
  --previous-config "$private_adapter_config" --helper-receipt "$writer_receipt" \
  --runtime-release "$protected_release" --output "$private_inspection"
```

Use private operator-resolved paths and the installer receipt. Inspection does
not save a reminder or request permission. If macOS has not authorized this
separate writer, the operator prepares one `--request-permission` inspection and
the human approves that exact app. Do not replace/re-sign the old reader or treat
its consent as writer consent. Then `--enable` with the same arguments plus
`--owner-authorization-sha256` prepares a private configuration file; only the
existing guarded channel activation applies it, preserving all prior source,
Google, reply and reminder grants. No synthetic member request is required.

Verify normal health, both loaded tools, current no-write helper inspection and
unchanged v1 reader state. A real authorized request is usable only after its
terminal status reports `succeeded` with native read-back. A pending or unknown
receipt is not success. On a regression, immediately stop authenticated-request
actions on the deployed schema-aware runtime, using the actual private instance:

```sh
"$protected_release/.venv/bin/python" -m bootstrap.request_actions --instance-dir "$instance_dir" disable
"$protected_release/.venv/bin/python" -m bootstrap.request_actions --instance-dir "$instance_dir" status
```

The control is read dynamically at confirmation, execution approval and final
native admission; it does not wait for maintenance. A native call already handed
off cannot be recalled: preserve its terminal or unknown result and never retry.
Then use `bootstrap.cos_reminders_write --disable --previous-config ...
--owner-authorization-sha256 ... --output ...` and guarded activation to revoke
only future CoS native writes. A prepared file alone does not change live policy.
Re-enabling request actions preserves the cutoff, so old pending actions do not
automatically resume. Retain the schema-aware runtime and attempt/terminal history
after any new write; an older binary cannot safely reinterpret those records.
Before any new records exist, the supported updater may restore its known-working
predecessor.

Focused proof covers the real application composition from all three channels,
actual sent/suppressed envelopes, retained attribution after revocation, native
callback/UTC component handling, and the protected predecessor's unchanged
reader launcher/worker running against the new runtime. Hosted tests use only
synthetic model/native stores and do not send messages or access household data.


## Natural-language reminder content

MODERNIZE: grounding skill 1.0.3 distinguishes creating the reminder from the
activity it describes. When no title is specified, the tool may use a minimal
concise title derived from the current authenticated instruction. An explicitly
specified title remains exact. It must not add sender, provider or other details
from earlier conversation. Directly requested quoted literal content is data;
quoted third-party instructions remain non-authority.

A request to create a reminder to email the requester “hello,” with “do not send
the email automatically,” authorizes the reminder only. The negative constraint
is retained in source evidence and may be copied into notes. It does not negate
the affirmative reminder request. A prohibition on creating the reminder still
rejects creation. A separately authorized composed workflow needs its own durable
trigger and effect authority; this confirmation proves neither.

This repair changes prompt/tool/workspace guidance, not persistent schemas,
exact source bindings, current grants, independent decisions or replay behavior.
Focused production and pinned-native fixtures use synthetic model completions;
they prove the composed boundary, not real model interpretation. A fresh genuine
request is needed to establish the live outcome. Old failed requests remain
failed and must not be replayed under a new pack version. Recovery may restore
the predecessor without erasing history; any already-created reminder remains
an external effect and is not undone by binary rollback.
