# Fishbrain Android supervisor

Supports Android 8.0 (API 26) and later. Other platform folders are templates.

## Setup and upgrades

Install and open Termux, enable allow-external-apps, reload settings, and grant RUN_COMMAND in the wizard. Install the brain, configure a model, install QQ and sign in.

All HTTP endpoints require Bearer authentication. The brain creates a private .api-token file (0600) in its directory. The app reads it through the authorized Termux command channel, never through HTTP. Never commit or log this token.

Upgrade both APK and brain together: install the new APK first, then update and restart the brain. Old APKs cannot access the authenticated API. Preserve config.toml, fish.db and .api-token. The installation wizard preserves existing config.toml.

The wizard stages source under .update and checks Python syntax before stopping the old supervisor. It activates the new code and restarts only the brain, then checks the authenticated API. Existing installations skip model configuration and QQ installation; their settings and QQ login remain intact. Environment checks must pass before installation can proceed.

## Development

Run flutter analyze --no-pub, flutter test --no-pub, then flutter build apk --release --no-pub after restoring locked dependencies. Run python -m unittest discover -s fish/tests -v from the repository root.

Synchronize fish source into assets/fish and register new modules in wizard.dart. Synchronize deploy scripts into assets/deploy. Bump the app version when embedded assets change.

Settings save only their own section. Unconfirmed delivery is an unknown outcome and must not be automatically retried. Legacy friend/group approvals without platform flags must be requested again.

Memory v2 adds event evidence, stable identities, scoped reflections, fact updates and follow-up intentions. The memory page shows provenance/status and supports correction, pinning, completion/cancellation and forgetting. Forgetting removes connected evidence and derived memories, so other facts from the same event can also be removed. The first database upgrade creates a SQLite backup and quarantines legacy reflections without reliable provenance. See [the memory design](../docs/memory-system.md) for migration, privacy boundaries and evaluation limits.

## Production signing

Local builds retain the existing debug signing identity for device update compatibility; they are not production releases. Provide android/key.properties with storeFile (relative to android/ or absolute), storePassword, keyAlias and keyPassword. Neither this file nor keystores belong in Git.

When configured, release builds use the production signing config. Set the Gradle property productionRelease=true (CI environment ORG_GRADLE_PROJECT_productionRelease=true) to fail closed when signing material is missing. Never generate a replacement certificate for an existing published identity as part of a code fix.
