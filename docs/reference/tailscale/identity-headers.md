---
source_url: https://tailscale.com/docs/features/tailscale-serve#identity-headers
fetched: 2026-04-22
page_title: Identity headers (Tailscale Serve)
---

# Identity headers

> **Note:** The originally requested URL (`https://tailscale.com/kb/1086/identity-headers`) now redirects to the generic `/docs` index — Tailscale has retired the standalone identity-headers KB article and moved its content into the `#identity-headers` section of the Tailscale Serve page. Content below is clipped from that section.

Serve traffic includes identity headers when serving traffic from your tailnet using Tailscale Serve. Funnel traffic, which is publicly available, does not include identity headers.

When you use Serve to proxy traffic to a local service running on your device, it adds a few Tailscale identity headers to the request sent to your backend. The destination server can use these headers to identify the Tailscale user associated with the request. If Serve finds the following headers on an incoming request, it will remove them for security reasons, to avoid header spoofing.

- `Tailscale-User-Login`: Filled with the requester's login name (for example, `alice@example.com`).
- `Tailscale-User-Name`: Filled with the requester's display name (for example, `Alice Architect`).
- `Tailscale-User-Profile-Pic`: Filled with the requester's profile picture URL, if their identity provider provides one (for example, `https://example.com/photo.jpg`).

If the values contain non-ASCII values, Tailscale might use RFC2047 "Q" encoding (for example, `=?utf-8?q?Ferris_B=C3=BCller?=`).

These identity headers are not populated for traffic originating from tagged devices.

You can use the identity headers with a custom backend or third-party services that offer authentication proxy authentication, such as Grafana.

Although identity headers are only populated for tailnet traffic, this includes traffic from external users who have accepted a share of your device. For example, if you share a device with a friend and have it configured with a Serve proxy, Tailscale populates the identity headers when your friend visits the Serve URL.

> **Best practice:** When you use the identity headers to authenticate to a backend service, it's best practice to only have the service listen on localhost. Otherwise, any user that can call your service directly (rather than with the Serve URL) could trivially provide their own values for these HTTP headers. By listening only on localhost, this limits tampering to only other services running on the Serve device, and not anyone on your LAN or tailnet.

## App capabilities header (related)

Serve traffic can also be configured to forward a header with selected app capabilities of the connected user or tagged device. Similar to identity headers, this isn't available for Funnel traffic, which is publicly available. Available in Tailscale v1.92 or later.

When you use Serve to proxy traffic to a local service running on your device, you can use the `--accept-app-caps` command line flag to specify which app capabilities of a user or tagged node Serve should forward. If a user or tagged node has been granted any of the specified app capabilities, Serve converts them to serialised JSON and forwards them in a `Tailscale-App-Capabilities` header.

If Serve finds the `Tailscale-App-Capabilities` header on an incoming request, Serve will remove it for security reasons, just like any Tailscale identity headers, to avoid header spoofing.
