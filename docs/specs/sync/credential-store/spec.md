# ParishKit Sync — Credentials

How Sync stores, binds, refreshes and shows the credentials modules use.
Overview: [Sync spec](../spec.md).

## Storage

- Each credential is one SQLite record encrypted with **AES-256-GCM**.
- Each record carries a key ID, so keys can rotate.
- The credential's name is bound in as associated data, so encrypted values
  cannot be swapped between records.
- Metadata stays in clear text: kind, scopes, who set it and when, last used,
  expiry and a fingerprint.

## Master key

- The installer generates a random 256-bit key into a **keyring directory**
  on the host (for example `/etc/parishkit-sync/keys/`, one file per key ID,
  mode 0400) and mounts that directory into the container **read-only as a
  secret**. The web app can read keys but never write, replace or remove
  them.
- The key lives outside the data volume, so a copy of the database alone
  cannot be decrypted.
- The setup wizard asks the administrator to escrow the key (for example in
  a password manager). A database backup is useless without it.
- **Rotation** is a **host-side** command (for example
  `parishkit-sync rotate-key`), like [recovery](../core/spec.md#host-side-recovery):
  1. It writes a new key file into the keyring directory, next to the old
     one, and restarts Sync.
  2. Sync encrypts new writes with the newest key and re-encrypts every
     existing record to it in the background. The Credentials page shows
     the key IDs in use and the re-encryption progress.
  3. Before re-encryption finishes, the command requires the administrator
     to confirm the new key is escrowed.
  4. The old key is then **retired, not deleted**: it stays in the keyring
     for decryption only, because retained database backups and the
     updater's rollback snapshot may still hold records encrypted with it.
     Key files are tiny. Deleting a retired key is a separate, explicit host
     command that warns which backups would become unreadable. Escrow covers
     every key in the keyring, not just the newest.

  The UI shows rotation status but cannot start or finish a rotation.
  Every step is audited.
- Anyone with root on the host can read the key and the credentials. That is
  accepted; host access is treated as already compromised.

## Slots and binding

- Modules declare named **credential slots** in their manifest (for example
  `parishsoft`, `google.directory`, `constant_contact`); see
  [modules](../modules/spec.md#manifest).
- The administrator binds each slot to a stored credential. One credential
  can serve several slots (for example one Google service account).
- Config never contains secrets.

## Kinds

- API keys (ParishSoft).
- Google service-account JSON plus a delegated subject.
- OAuth client and tokens (Constant Contact; the identity provider's client).
- Slack token.

## Refresh

The core refreshes OAuth access tokens before they expire and stores the new
tokens. A refresh failure raises an alert and fails the dependent steps
loudly.

## Display and reveal

- The UI shows metadata only by default.
- **Reveal** requires a recent sign-in and writes an audit record.
- Setting, replacing, revealing and deleting a credential are all audited
  (see [logging](../logging/spec.md#audit)).

## Shared code

One encryption helper, shared with Stewardship if it fits without changing
Stewardship's behavior (Stewardship's general-encryption key handling is the
prior art).

## Rejected approaches

- **External KMS or Vault:** it still needs a host credential to unlock it,
  so on a single VM it adds cost without protecting against root.
- **Credentials as files on disk** (the legacy layout): hard to manage from
  the UI, and readable by anything that can read the data volume.
