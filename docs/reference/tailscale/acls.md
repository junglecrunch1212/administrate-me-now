---
source_url: https://tailscale.com/docs/features/tailnet-policy-file
fetched: 2026-04-22
page_title: Tailnet policy file
---

# Tailnet policy file

Last validated: May 21, 2025

> **Note:** The URL originally requested (`https://tailscale.com/kb/1336/tailnet-policy-file`) redirects to an unrelated page (`/docs/features/access-control/device-management/how-to/set-up-qr-code`). The canonical URL for the tailnet policy file documentation is `/docs/features/tailnet-policy-file`. Content below is from that canonical URL.

The tailnet policy file is a centralized human JSON (HuJSON) configuration file that stores parameters, policies, and settings for your Tailscale network (known as a tailnet).

Owners, Admins, and Network admins can manage your tailnet policy file from the Tailscale admin console. You can also manage the tailnet policy file with GitOps using GitHub, GitLab, or Bitbucket.

The tailnet policy file is organized into multiple top-level sections, each offering different functionality. You can use the various sections of the tailnet policy file to:

- Define named groupings of users, devices, and network segments with tags, groups, and IP sets.
- Define access control policies at the network layer using ACLs.
- Define access control policies at the network layer and application layer using grants.
- Assign aliases to IP addresses and subnets (using the `hosts` section).
- Define device posture rules.
- Specify who can use Tailscale SSH.
- Specify who can use which tags to authenticate devices.
- Specify who can bypass the approval process to advertise subnet routers and exit nodes.
- Apply additional attributes called node attributes to devices and users.
- Write tests to make assertions about access policies (ACLs and Tailscale SSH) that should not change.
- Define tailnet-wide policy options (such as disabling IPv4).

Using the different sections of the tailnet policy file in unison lets you manage your tailnet in a modular and fine-grained manner. For example, you can define a custom group of users, then create an access control policy to specify how the users in that group can traverse the resources in your tailnet.

## Sections

The following table provides an overview of each top-level section of the tailnet policy file.

| Section | Name | What it's for | Resources |
| --- | --- | --- | --- |
| `acls` | Access control lists (ACLs) | Create network-level access control policies. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/acls) |
| `autoApprovers` | Auto approvers | Specify who can bypass the approval process to advertise subnet routers, exit nodes, and app connectors. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/auto-approvers) |
| `grants` | Grants | Define network-level and application-level access control policies. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/grants) |
| `groups` | Groups | Define named groups of users, devices, and subnets to target in access control policies and other definitions. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/groups) |
| `hosts` | Hosts | Define named aliases for devices and subnets. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/hosts) |
| `ipsets` | IP sets | Define named network segments to target in access control policies and other definitions. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/ip-sets) |
| `nodeAttr` | Node attributes | Apply additional attributes to devices and users. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/node-attributes) |
| `postures` | Device posture policies | Define device posture rules to target in access control policies. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/device-posture) |
| `ssh` | Tailscale SSH | Specify who can use Tailscale SSH. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/tailscale-ssh) |
| `sshTests` | Tailscale SSH tests | Write tests to make assertions about Tailscale SSH that should not change. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/ssh-tests) |
| `tagOwners` | Tag owners | Define who can assign which tags to devices in your tailnet. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/tag-owners) |
| `tests` | Access control tests | Write tests to make assertions about access policies (ACLs and network-level grants) that should not change. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/tests) |

There's also additional sections for network policy options, such as disabling IPv4 and customizing the DERP map. In most cases, these settings are unnecessary.

| Section | What it's for | Resources |
| --- | --- | --- |
| `derpMap` | Customize the DERP servers that a tailnet uses. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/derp-map) |
| `disableIPv4` | Disable using IPv4 in a tailnet. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/disable-ipv4) |
| `OneCGNATRoute` | Modify the routes the Tailscale clients generate. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/one-cgnat-route) |
| `randomizeClientPort` | Control whether devices prefer a random port number or the default 41641 for WireGuard traffic. | [Syntax reference →](https://tailscale.com/docs/features/tailnet-policy-file/randomize-client-port) |
