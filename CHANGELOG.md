# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is below
1.0, a minor release may change behaviour.

Each release is published as a container image:
`ghcr.io/m8-t/kiosk-home:<version>`.

## [0.1.0] - 2026-10-15

### Added

- Start page for a wall tablet with link tiles read from a JSON file, re-read on every
  request so ConfigMap updates apply without a restart.
- One card per Philips Hue room with an on/off toggle and a brightness slider.
- Server endpoints for links, rooms and room updates, backed by the Hue CLIP v2 API.
- Container image, Kubernetes manifests and a release workflow.
