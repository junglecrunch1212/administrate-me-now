# Retained CoS Reminders provenance after recovery

MODERNIZE the historical-attribution recovery boundary: a failed source activation
can preserve valid append-only facts while restoring the former configuration.
Removing present access does not erase historical ownership. This repair attributes
one existing first-poll `list.created@2`; it enables no adapter, context read or write.

Use the clean protected successor checkout with the installed predecessor's Python.
`bootstrap.cos_apple_source_history` supports validation before deployment and an
explicit append of `adapter.source_ownership_reconciled@1`. The new event has no
reactive consumer and does not change the old fact, schema, checkpoint or grants.
The actual predecessor runtime's read and projection replay are tested on an
isolated encrypted backup containing this previously unknown audit type.

Prepare a private `cos-apple-source-history-repair-v1` request with:

- `controller_commit` and `expected_active_commit`: exact protected controller and
  current predecessor commits.
- `source_event_id`, `authentication_event_id`: the retained first list fact and
  its subsequent `adapter.authenticated@3` receipt.
- `activation_id`, `activation_journal_sha256`, `activation_request_sha256`: the
  exact recovered operation, its archived private journal bytes, and original
  ActivationRequest bytes.
- `recovery_authorization_sha256`: the owner's existing recovery authorization.

Supply the original source ownership evidence file; its bytes must match the hash
inside the original selection. The command reads the archived journal from
`app/cos-channel-operation/history/<activation_id>.json`, reconstructs the prepared
configuration from the original request and prior configuration, and checks both
its journal hash and the authentication receipt's exact configuration/device/source
hashes. Wrong owner, account, native source, device, authority, event or release
fails closed. All evidence inputs must be owner-only regular files.

```sh
"$INSTALLED_PYTHON" -m bootstrap.cos_apple_source_history \
  --instance-root "$INSTANCE_ROOT" --request "$PRIVATE_REPAIR_REQUEST" \
  --activation-request "$ORIGINAL_ACTIVATION_REQUEST" \
  --ownership-evidence "$ORIGINAL_OWNERSHIP_EVIDENCE"
```

Validation records nothing. Repeat with `--append` to record one idempotent,
service-private provenance receipt, under the existing operation lock, while the
healthy predecessor remains online. A conflicting receipt fails closed. The
recorded receipt carries only exact provenance and evidence hashes; external
artifacts are no longer required for future historical attribution or restore.
The original source content is not copied into it or exposed in reports.

Then run the ordinary HTR-10 export and review from the protected successor; no
special override or manifest argument is needed. Proceed through the supported
updater and existing activation. Current source policy still controls context and
retained work; this receipt never restores revoked access. This is a bounded
repair for one retained first-poll fact, not a general retired-provider grant.

Recovery preserves the receipt and original facts. The predecessor can retain and
replay it, though only the successor understands its historical attribution. If
activation regresses, restore the known-working configuration with Apple polling
disabled; keep history intact and use the protected successor controller for HTR-10.
