# Member-device testing

## Authority and scope

**PRESERVE:** the tagged central Host is infrastructure, not a member login;
member access comes through the reviewed trusted ingress. See
[Bootstrap and topology contract](BOOTSTRAP-AND-TOPOLOGY-CONTRACT.md#deployment-boundary),
[UX behavior contract](UX-BEHAVIOR-CONTRACT.md) UX-35, and
[Acceptance contract](ACCEPTANCE-CONTRACT.md) AC-18 and AC-21.

**MODERNIZE:** make the location of browser execution, remote control, and
return of inspection results explicit in development work. This adds no
product runtime, member authority, provider capability, or deployment approval.

## Three environments

| Environment | Work |
| --- | --- |
| Development project | Coordinate changes, review code, request bounded remote checks, and inspect returned results. |
| Disposable lab or Codex cloud | Run candidate code through the production composition root with synthetic members and sources. Run mutation, responsive-layout, and regression tests here. |
| Authorized member device | Run the browser against the normal deployed Tailscale HTTPS URL for owner-authorized, read-only acceptance. |

Do not run candidate services on the family Host. A desktop viewport or mobile
emulation is useful layout evidence but does not prove actual iPhone Safari,
macOS permissions, bridge enrollment, or another member's device behavior.

## Connection inventory

Keep actual device names, addresses, logins, task IDs, SSH configuration,
expected member bindings, and private artifact locations in an owner-controlled
file outside Git, such as `~/.config/administrateme/member-testing.json`.
Restrict the file to the owning account. Store key references only; never
store private key contents, browser cookies, provider credentials, or source
content in the inventory. Use generic member aliases in repository material.

Record these separately for each approved test target:

- Device and operating system; expected member and role.
- Normal application HTTPS origin and expected deployed release.
- Browser profile and the device where that browser actually executes.
- Codex remote host and an existing relevant member-acceptance task, if used.
- Optional SSH alias, its direction, and which operations it supports.
- How screenshots/reports return to the development project.
- Last verification time and result for each connection; unknown is not pass.

Use an available existing member-acceptance task for bounded related work.
Create a new user-owned task only when the user requests one. A task handoff
requires a matching saved repository project on the destination host; it is
not required merely to request a member-device check.

## Verify before relying on a connection

1. Confirm the intended member device is online. A Tailscale peer entry proves
   presence, not SSH availability, browser control, or member authorization.
2. Test remote execution with a harmless device-name/status command. Confirm
   that the command ran on the expected device and that its result returns.
3. Open the normal HTTPS URL in a browser executing on that device. Inspect
   the displayed authenticated member, role, Tailscale ingress, reading member,
   and action member. Stop on unexpected identity or view-as.
4. Confirm the expected deployed release and observation state using the
   authenticated surface or the separately authorized read-only operator check.
   Report unavailable release evidence explicitly.
5. Navigate the requested surface, read its accessible state, and inspect a
   rendered screenshot. Exercise only navigation and display controls within
   the live read-only scope.
6. Return a concise report and any authorized screenshots to private storage
   accessible to the coordinating task. Open the returned image there before
   claiming that the coordinator can visually review the remote result.

A command being accepted, a task showing completed, or a screenshot path on a
different machine is not proof that results were received. If task summaries
omit output, try the supported direct task-message return path and a verified
private file-transfer path. Report each unverified part separately.

## Remote access choices

Codex remote task execution uses the remote host's tools and browser setup.
Controlling the central Host's ChatGPT app from a member PC leaves execution on
the Host. It does not move the browser or adopt the PC's member identity.

Ordinary SSH commands may be launched from a local project when the outbound
connection is configured. SSH is directional: member-to-Host access does not
prove Host-to-member access. On Windows, distinguish the OpenSSH client from
the OpenSSH Server (`sshd`) service. An existing verified direction can also
return artifacts without enabling another inbound service.

Prefer existing authenticated connections. When SSH setup is required, verify
the host key, use a dedicated key and least-privilege access, and scope any new
firewall access to the intended control device over Tailscale. Preserve
unrelated connections. Keep browser debugging endpoints on loopback; do not
publish them to the tailnet or Internet.

Never forge Tailscale identity headers, retag the central Host as a member,
copy another member's browser session, or treat a different Chrome profile as
a different Tailscale identity. Honor browser and operating-system security
blocks; do not bypass them with another tool or transport.

## Acceptance report

Record device execution verified, control available, normal HTTPS ingress,
expected identity, release evidence, observation evidence, requested routes,
rendered inspection, console/request failures where available, and artifact
return verified. Give each item PASS, FAIL, or NOT VERIFIED with a concrete
reason. Report what was exercised and whether any mutation was attempted.

Keep screenshots and traces with household content out of Git and hosted CI.
Return only the content needed for the authorized review. Hosted CI and
implementation subagents never access the live family instance or credentials.
The browser connection grants no deployment, provider-write, enrollment,
permission-change, or unrelated repair authority.

## Product documentation

- [OpenAI remote connections](https://learn.chatgpt.com/docs/remote-connections)
- [Tailscale Serve identity headers](https://tailscale.com/docs/features/tailscale-serve#identity-headers)
- [Microsoft OpenSSH for Windows](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)
- [Playwright trace viewer](https://playwright.dev/docs/trace-viewer)
