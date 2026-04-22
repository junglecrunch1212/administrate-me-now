---
source_url: https://tailscale.com/docs/features/exit-nodes
fetched: 2026-04-22
page_title: Exit nodes (route all traffic)
---

# Exit nodes (route all traffic)

Last validated: Dec 15, 2025

> **Note:** The URL originally requested (`https://tailscale.com/kb/1103/exit-nodes`) now redirects to `/docs/features/exit-nodes`. Content below is from the current canonical URL.

**Plan availability:** Exit nodes are available for all plans.

By default, Tailscale acts as an overlay network: it only routes traffic between devices running Tailscale, but doesn't touch your public internet traffic, such as when you visit Google or Twitter. The overlay network configuration is ideal for most people who need secure communication between sensitive devices (such as company servers or home computers), but don't need extra layers of encryption or latency for their public internet connection.

However, there might be times when you want Tailscale to route your public internet traffic. For example, you might want to route all your public internet traffic if:

- You're in a coffee shop with untrusted Wi-Fi.
- You're traveling overseas and need access to an online service (such as banking) only available in your home country.

You can route all your public internet traffic by setting a device on your network as an exit node, then configuring other devices to send traffic through it. When you route all traffic through an exit node, you're effectively using default routes (`0.0.0.0/0`, `::/0`), similar to how you would if you were using a typical VPN.

## Benefits

- **Secure all traffic:** Exit nodes secure all traffic, including traffic to internet sites and applications.
- **Scale globally:** Deploy exit nodes around the globe to meet your scale and geographical needs.
- **Increase visibility:** Destination logging provides increased visibility of traffic across the tailnet and forensic analysis during security incidents.

## Use cases

- **Traveling workforce:** Ensure all internet traffic is secured for your traveling workforce regardless of the physical network they're using.
- **Testing from different locations:** Test your applications from different locations by deploying and selecting exit nodes around the globe.
- **Meet compliance needs:** If you have regulatory or compliance needs that require your workforce to use a VPN, exit nodes can help.

## How it works

The exit node feature lets you route all traffic through a specific device on your Tailscale network (known as a tailnet). The device routing your traffic is called an exit node.

There are many ways to use exit nodes in a tailnet. For example, you can:

- Route all non-Tailscale traffic through an exit node.
- Use suggested exit nodes to automatically use the best exit node based on client information, such as location and latency.
- Force devices to use an exit node based on system policies, which you can deploy using mobile device management (MDM) solutions.

For security purposes, you must opt in to exit node functionality. For example:

- Every device must explicitly opt in to using an exit node.
- A device must advertise itself as an exit node.
- An Owner, Admin, or Network admin must allow a device to be an exit node for the tailnet.

By default, exit nodes capture all your network traffic that isn't already directed to a subnet router or app connector. You can also route specific network traffic using subnet routers or app connectors. On Android devices, you can also use app-based split tunneling.

### Local network access

By default, the device connecting to an exit node won't have access to its local network. If you want to allow the device access to its local network when routing traffic through an exit node, enable exit node local network access.

You can enable the Allow Local Network Access setting from the Exit Nodes section of your Tailscale client. You can also enable this setting by passing `--exit-node-allow-lan-access` to `tailscale up` or `tailscale set`.

## Get started

Refer to the [Use exit nodes quickstart guide](https://tailscale.com/docs/features/exit-nodes/how-to/setup) for basic instructions on how to configure and use exit nodes.

To get started with exit nodes:

1. Understand the prerequisites.
2. Configure a device to act as an exit node.
3. Allow the exit node from the admin console.
4. Configure other devices to use the exit node.

### Prerequisites

Before you can configure an exit node, you must:

- Set up a Tailscale network (known as a tailnet).
- Ensure both the exit node and devices using the exit node run Tailscale v1.20 or later.
- Ensure the exit node is a Linux, macOS, Windows, Android, or tvOS device.
- Ensure you allow (intended) users to use the exit node. Check your tailnet's ACLs and grants. If your tailnet is using the default access control policy, users of your tailnet already have access to any exit nodes that you configure. If you have modified the access control policies of your tailnet, ensure you create an access rule that includes exit node uses in the `autogroup:internet`. They do not need access to the exit node itself to use the exit node.

## Configure an exit node

Use the following steps to configure an exit node:

1. Install the Tailscale client.
2. Advertise the device as an exit node.
3. Allow the exit node.
4. Use the exit node.

You can also get a suggested exit node.

### Install the Tailscale client

Download and install Tailscale onto the device you plan to use as an exit node. Instructions vary per OS (Android, Linux, macOS, tvOS, Windows).

### Advertise a device as an exit node

Open the Tailscale client on the device, go to Exit Node and select Run as exit node. Instructions vary per OS (Android, Linux, macOS, tvOS, Windows).

### Allow the exit node from the admin console

You must be an Admin to allow a device to be an exit node. If the device is authenticated by a user who can approve exit nodes in `autoApprovers`, the exit node will automatically be approved.

1. Open the Machines page of the admin console and locate the exit node.
2. Locate the Exit Node badge in the machines list or use the `property:exit-node` filter to list all devices advertised as exit nodes.
3. From the menu of the exit node, open the Edit route settings panel, and enable Use as exit node.

### Use the exit node

Each device must enable the exit node separately. The instructions for enabling an exit node vary depending on the device's operating system (Android, iOS, Linux, macOS, tvOS, Windows).

General pattern (example: Android):

1. Open the Tailscale app on the device and go to the Exit Node section.
2. Select the exit node that you want to use.
3. If you want to allow direct access to your local network when routing traffic through an exit node, toggle Allow LAN access on.
4. On the app home screen, confirm that the selected device displays in the Exit Node section. When an exit node is being used for the device, the section will turn blue.
5. To stop a device from using an exit node, go to the Exit Node section and select None.

The option to use an exit node only displays if there's an available exit node in your tailnet.

You can verify that your traffic is routed by another device by checking your public IP address using online tools. The exit node's public address displays rather than your local device's IP address. You can turn off routing through an exit node by selecting None from the Exit Node drop-down.

## Destination logging in network flow logs

**Plan availability:** Destination Logging is available for the Premium and Enterprise plans.

By default, destination logging is disabled for traffic flowing through an exit node across all tailnets, for privacy, abuse, and security purposes. Tailnets on the Enterprise plan can, however, enable destination logging across the tailnet for increased visibility of traffic across the tailnet and forensic analysis during security incidents. Destinations are logged in Network flow logs.

You must enable log streaming before using exit node destination logging.

To enable destination logging for exit nodes:

1. Open the Logs page of the admin console.
2. Select Network flow logs.
3. Select the Logging Actions menu, then select Enable exit node destination logging.

To disable destination logging for exit nodes:

1. Open the Logs page of the admin console.
2. Select Network flow logs.
3. Select the Logging Actions menu, then select Disable exit node destination logging.

## Caveats

Tailscale support for running exit nodes on Android is still undergoing optimization. Make sure you plug the device into a power source if you plan to use it as an exit node for an extended time. Android exit nodes are limited to userspace routing. Running an exit node on an Android device is not performant — it may be too slow for most cases.

**Userspace:** On Android, the exit node is implemented in userspace, which differs from the default Linux exit node implementation and is not as mature or fully optimized. For details, refer to [Kernel vs. netstack subnet routing and exit nodes](https://tailscale.com/kb/1320/performance-best-practices#subnet-routers-and-exit-nodes).

### Expired device keys

When a connector's (such as, app connector, subnet router, exit node) key expires, the connector's advertised routes remain configured on other devices but become unreachable (known as "fail close" policy). Tailscale keeps these routes in place intentionally because removing them could leak traffic to untrusted networks.

To prevent disruption from this behavior, disable key expiry on the connector or configure high availability. If you prefer to withdraw routes when a key expires, you can use the admin console or API to enable and disable advertised routes when certain conditions are met.
