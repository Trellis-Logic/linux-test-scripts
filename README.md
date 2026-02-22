# Linux Device Tests
Scripts used to test Linux based devices

# Setup Python

Run 
```
pip3 install -r requirements.txt
```
## Rolling Reboot Tests

Run
```
python3 rolling_reboot_test.py --device <ip addr>  --user user --password password
```
Where `user` and `password` describe an SSH access user

### Running with non-root Accounts

When running with non root accounts, add a line like this to your /etc/sudoers file:
```
user ALL=NOPASSWD: /sbin/reboot
```
Where `user` is the username passed with the `--user` argument.

Then run the test case with the `--sudo` argument specified to use sudo with the reboot command

## Update Tests

### swupdate (tegra platforms)

Run
```
python3 swupdate_test_tegra.py --device <ip addr> --updatefile <file>
```

To run a swupdate test for OE4T tegra based platforms which include swupdate with A/B rootfs.

The test will perform 100 consecutive updates, verifying boot and rootfs slots are switched synchronously after each.

### swupdate sequence from GitHub Actions artifacts

`swupdate_test_sequence.py` can download artifacts from a GitHub Actions run and apply them to a connected device in order.

Artifacts are expected to be named like:
`<project>-<branch>-<image>-<machine>-swu`

For a given `--project`, `--image`, and `--machine`, it will attempt to apply updates in this release order:
- `kirkstone`
- `scarthgap-l4t-r35.x` (if present)
- `scarthgap` (if present)
- `master`

Example:
```
export GITHUB_TOKEN=<token>
python3 swupdate_test_sequence.py \
  https://github.com/Trellis-Logic/kas-demos/actions/runs/<run_id> \
  --device <ip addr> \
  --project swupdate-rootfs-overlay-oe4t \
  --image demo-image-base \
  --machine jetson-orin-nano-devkit-nvme
```

#### Setting the GitHub token

Artifact download via the GitHub API typically requires authentication.

You can provide the token in any of these ways:
- Create a local `.env` file (in the directory you run the script from, or alongside the script) containing `GITHUB_TOKEN=<token>`, for example:
```
GITHUB_TOKEN=<token>
```
- Environment variable `GITHUB_TOKEN` (preferred) or `GH_TOKEN`
- CLI argument `--github-token <token>`

The token must have permission to read Actions artifacts for the target repo/run (for fine-grained tokens this is typically an “Actions: read” permission; for classic tokens it may require broader `repo` access depending on repo visibility).

Avoid hard-coding tokens in scripts or committing them to the repo.

If you use the `.env` approach, this repo ignores `.env` via `.gitignore`.

#### Download/extract only (no device interaction)

```
export GITHUB_TOKEN=<token>
python3 swupdate_test_sequence.py \
  https://github.com/Trellis-Logic/kas-demos/actions/runs/<run_id> \
  --project swupdate-rootfs-overlay-oe4t \
  --image demo-image-base \
  --machine jetson-orin-nano-devkit-nvme \
  --download-only
```

#### Using a local artifact directory

If you have already downloaded/extracted artifacts locally, pass the directory and use `--skip-download`:

```
python3 swupdate_test_sequence.py /path/to/artifacts \
  --skip-download \
  --device <ip addr> \
  --project swupdate-rootfs-overlay-oe4t \
  --image demo-image-base \
  --machine jetson-orin-nano-devkit-nvme
```
