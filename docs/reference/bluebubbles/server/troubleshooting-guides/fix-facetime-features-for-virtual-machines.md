---
**Source:** https://github.com/BlueBubblesApp/bluebubbles-docs/blob/main/server/troubleshooting-guides/fix-facetime-features-for-virtual-machines.md

**Fetched:** 2026-04-22

**License:** Apache-2.0 (see licenses-legal.md in-repo)

**Note:** Verbatim mirror for AdministrateMe docs/reference/ (prompt 00.5). Do not edit here.
---

---
description: >-
  This document will guide you on possibly fixing the FaceTime features when
  using a macOS virtual machine.
---

# Fix FaceTime Features for Virtual Machines

{% hint style="danger" %}
The FaceTime features **require macOS Monterey and newer**&#x20;
{% endhint %}

{% hint style="warning" %}
Keep in mind, the FaceTime features are experimental and may not always be 100% reliable.
{% endhint %}

### Why Wouldn't the FaceTime Features Work in a Virtual Machine?

Virtual machines typically do not have a microphone or webcam plugged into it, so when FaceTime tries to load and use the default audio/video devices, it fails and causes calls to fail.

### What Can I Do to Fix It?

A possibly way to fix the issue is to install "virtual" audio/video devices on your Mac so that FaceTime can use those.

{% hint style="info" %}
Complete the following steps on your macOS virtual machine
{% endhint %}

1. Download & install a virtual audio device: [https://vb-audio.com/Cable/](https://vb-audio.com/Cable/)
2. Download & install a virtual video device: [https://obsproject.com/](https://obsproject.com/)
   * Open OBS and Start the Virtual Camera
     * You only need to do this once to register it with the system and show in FaceTime
3. Reboot your Mac
4. Open FaceTime
5. Using the FaceTime status bar menu...
   * Select the OBS virtual camera as your video source
   * Select the VB Audio virtual audio device as your audio source

Now that you have virtual devices setup for both your audio/video inputs, FaceTime calling/answering should be more reliable.
