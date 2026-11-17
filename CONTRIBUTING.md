# Contributing

Contributions are welcome, from a branch in this repository or from a fork. Before you open a pull
request, read the development workflow in [AGENTS.md](AGENTS.md) (Steps 1–6) and the ownership
disclaimer in [README.md](README.md).

## Commits must be signed

`main` is protected by a ruleset that requires **verified signatures** on every commit. If one
commit in your pull request is not signed, GitHub shows:

> **Merging is blocked** — Commits must have verified signatures.

An approval does not unblock it, and a maintainer cannot fix it with "Squash and merge": GitHub
only allows a squash merge on a protected branch for the author of the pull request. Sign your
commits **before** you push.

### One-time setup (SSH signing)

SSH signing is the simplest option, because you can use the SSH key you already use for GitHub.

1. Tell git to sign with your SSH key. Run this in your clone, or add `--global` to use it for every
   repository:

   ```bash
   git config gpg.format ssh
   git config user.signingkey ~/.ssh/id_ed25519.pub   # path to your PUBLIC key
   git config commit.gpgsign true
   ```

2. Upload the same public key to GitHub as a **signing key**: **Settings → SSH and GPG keys → New
   SSH key**, and set **Key type** to **Signing Key**. A key that is already registered as an
   *Authentication Key* is not enough: add it a second time as a *Signing Key*.

3. Make sure the email in your commits is a **verified email** on your GitHub account. GitHub marks
   a signed commit as *Unverified* if its author email is not on your account:

   ```bash
   git config user.email     # must appear in Settings → Emails
   ```

GPG keys also work. See the GitHub documentation:
[About commit signature verification](https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification).

### Check before you push

```bash
git cat-file commit HEAD | grep -q '^gpgsig' && echo signed || echo NOT signed
```

`git log --show-signature` may print *"No signature"* or an error for SSH signatures even when the
commit is signed. That only means git has no `gpg.ssh.allowedSignersFile` to check against locally.
The `gpgsig` header above is the reliable local check.

### If you already pushed unsigned commits

Do the one-time setup, then sign the existing commits again and force-push the branch of the pull
request:

```bash
git fetch origin            # use the remote that points to kastenhq/blueprint-ai
git rebase --exec 'git commit --amend --no-edit -S' origin/main
git push --force-with-lease
```

If the pull request has only one commit, `git commit --amend --no-edit -S` followed by
`git push --force-with-lease` is enough.

The pull request updates by itself. On the commits tab, each commit must show the **Verified**
badge. You can also check from the command line:

```bash
gh api repos/kastenhq/blueprint-ai/pulls/<PR_NUMBER>/commits \
  --jq '.[] | [.sha[0:7], .commit.verification.verified, .commit.verification.reason] | @tsv'
```

Every line must show `true`. If a line shows `unsigned`, the commit has no signature. If it shows
`unknown_key`, the key is not uploaded as a *Signing Key*. If it shows `bad_email` or
`unverified_email`, the commit email is not verified on your GitHub account.
