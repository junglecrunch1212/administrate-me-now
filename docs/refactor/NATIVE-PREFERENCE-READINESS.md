# Retaining existing native preferences during maintenance

The operator may reconcile an authentic deployment binding after already-authorized
native preferences were added. This records existing configuration; it writes no
native configuration, changes no grants, and refreshes no acceptance date or
provider-effect window. The closed additions are thinkingDefault `high`, audit
messages `direct`, skill nodeManager `npm`, enabled loop detection and swarm, and
enabled tool search in `directory` mode. Existing values cannot be replaced or
removed. Any other native or deployment coordinate change is rejected.

Use the selected deployment's Python with a clean protected-main controller:

```sh
python -m bootstrap.workstation_browser reconcile-native-preferences \
  --instance-root "$INSTANCE_ROOT" \
  --previous-native-config "$AUTHENTIC_PRIOR_RAW" \
  --approved-patch "$APPROVED_DELTA" \
  --approved-patch-sha256 "$APPROVED_DELTA_SHA256" \
  --authorization-reference "$EXISTING_OWNER_REQUEST" \
  --current-snapshot "$VALIDATED_NATIVE_SNAPSHOT"
```

The private approved-delta object contains exactly `baseHash` (SHA256 of the
unaltered authentic prior raw bytes) and `raw` (JSON text containing only the
approved additions). The current native `config.get` snapshot must say `valid:
true` and bind the exact current raw hash. All inputs and parent directories are
private owned regular coordinates. The prior raw must reconstruct the entire
saved readiness binding, including the unchanged host, instance and release.

Review the content-free preflight, then repeat the same command with
`--apply --plan-sha256 "$PLAN_SHA256"`. The existing deployment lock and shared
native/browser readiness journal prevent competing operations. A crash is
recovered by repeating that exact command with the same protected controller and
inputs; no new evidence, dates or authority are manufactured. Browser-only
reconciliation cannot resume the distinct preference contract.

The receipt retains original readiness bytes and both complete snapshot hashes.
Recovery completes that exact metadata publication. Restoring older readiness
metadata would be valid only if its entire bound deployment coordinates still
match; this command does not offer an old-config restore or discard current
settings. If coordinates drift, stop and preserve the pending journal for a
scoped repair. Neither a generic attestation nor a manual readiness edit is a
substitute. After success, normal supported maintenance remains responsible for
fresh operation-time health and current authority checks.
