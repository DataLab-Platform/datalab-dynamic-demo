# Releasing Dynamic mode demo

> **Golden rule: never create a GitHub release by hand.** Push the tag, wait for the draft release prepared by the CI, then publish that draft.

A release takes two steps, both run by [`.github/workflows/release.yml`](.github/workflows/release.yml), which relies on the [release actions](https://github.com/DataLab-Platform/release-actions) shared by the DataLab Platform projects:

1. **Prepare**: pushing a release tag checks the tag, runs the tests, builds the wheel, attests its build provenance and creates a **draft** GitHub release with the wheel and its `SHA256SUMS`. Nothing is published at this stage.
2. **Publish**: when a maintainer publishes the draft release, its wheel is checked (checksum and attestation), and the summary of the run gives the entry of the [DataLab plugin catalog](https://github.com/DataLab-Platform/plugins), with the SHA-256 digest of the checked wheel. A release created by hand has no such file, so the check fails.

DataLab users install the wheel of a release with **Plugins > Configure plugins... > From file**, or from the **Catalog** tab once the release is listed in the catalog.

## Releasing a final version

1. Through a pull request to `main` (as usual):
   * set the version `X.Y.Z` in `pyproject.toml` and `__version__ = "X.Y.Z"` in `src/datalab_dynamic_demo/__init__.py`;
   * write the `## [X.Y.Z] - YYYY-MM-DD` section of `CHANGELOG.md`: it becomes the description of the GitHub release, and the release is refused if it is missing.
2. Tag `main` and push the tag (`-s` signs the tag, use `-a` otherwise):

   ```console
   git switch main
   git pull
   git tag -s vX.Y.Z -m "Dynamic mode demo vX.Y.Z"
   git push origin vX.Y.Z
   ```

3. Follow the **Prepare vX.Y.Z** run in the [Actions](https://github.com/DataLab-Platform/datalab-dynamic-demo/actions/workflows/release.yml) tab. When it succeeds, its summary links to the draft release.
4. Review the draft release (title, description, wheel and `SHA256SUMS`), then click **Publish release**.
5. Publishing starts the **Publish vX.Y.Z** run: once it succeeds, open a pull request on the [plugin catalog](https://github.com/DataLab-Platform/plugins) with the entry given in its summary.

## Release candidates

A tag `vX.Y.Z-rcN` (for example `v0.3.0-rc1`) follows the same two steps, with these differences: the version must be `X.Y.ZrcN` (for example `0.3.0rc1`) in both files, the tag may be on any branch, the `CHANGELOG.md` section is optional, the draft release is marked as a pre-release, and a release candidate is not listed in the catalog.

## When something goes wrong

| Symptom | What to do |
| --- | --- |
| The **Prepare** run fails (tag, version, changelog, tests or build) | Nothing was published. Delete the tag (`git push --delete origin vX.Y.Z`, then `git tag -d vX.Y.Z`), fix the problem, then tag again. A draft release left by a failed attempt is replaced automatically. |
| A problem is found while reviewing the draft release | Delete the draft release and the tag, fix the problem, then tag again. |
| The **Prepare** run fails with "Release already published" | A release was created by hand for this tag. Delete it, then re-run the failed jobs of the **Prepare** run. If immutable releases are enabled on the repository, the tag cannot be reused: release version X.Y.Z+1 instead. |
| The **Publish** run fails with "Release not prepared by the release workflow" | The published release was created by hand: do not list it in the catalog. Delete it, then re-run the **Prepare** run of the tag (or, with immutable releases, release version X.Y.Z+1). |
