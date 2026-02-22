#!/usr/bin/env python3

import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from device_test import DeviceTest
from swupdate_test_tegra import SwupdateTestTegra


@dataclass(frozen=True)
class GithubRunRef:
    owner: str
    repo: str
    run_id: int


@dataclass
class LocalArtifact:
    name: str
    zip_path: Optional[Path]
    extracted_dir: Path


class SwupdateTestSequence(SwupdateTestTegra):
    """Perform a cross-release SWUpdate sequence using artifacts from a GitHub Actions run.

    Typical usage:
      python3 swupdate_test_sequence.py \
        https://github.com/Trellis-Logic/kas-demos/actions/runs/22260086758 \
        --device <ip> --project swupdate-rootfs-overlay-oe4t --image demo-image-base \
        --machine jetson-orin-nano-devkit-nvme

    Notes:
    - Downloading artifacts usually requires a GitHub token. Provide one via --github-token
      or env var GITHUB_TOKEN / GH_TOKEN.
    - This script expects each relevant artifact to contain a .swu file for the requested --image.
    - Artifact naming is assumed to be: <project>-<branch>-<image>-<machine>-swu
      where <branch> is one of the sequence items (kirkstone, scarthgap-l4t-r35.x, scarthgap, master).
    """

    RELEASE_ORDER: Sequence[str] = (
        "kirkstone",
        "scarthgap-l4t-r35.x",
        "scarthgap",
        "master",
    )

    # Prefer dedicated artifacts that only contain the .swu (artifact name ends with this suffix).
    SWU_ARTIFACT_SUFFIX: str = "-swu"

    def get_parser(self):
        if self.argparser is None:
            p = DeviceTest.get_parser(
                self,
                "swupdate_test_sequence.py",
                "Downloads artifacts for a GitHub Actions run (or uses a local artifact directory) and runs a SWUpdate sequence",
            )

            p.add_argument(
                "run_ref",
                help=(
                    "GitHub Actions run URL / run id, or a local directory containing downloaded artifacts (zip files and/or extracted dirs). "
                    "Example: https://github.com/Trellis-Logic/kas-demos/actions/runs/22260086758"
                ),
            )

            p.add_argument(
                "--project",
                default="swupdate-rootfs-overlay-oe4t",
                help=(
                    "Project name used to build artifact names (default: swupdate-rootfs-overlay-oe4t). "
                    "Artifacts are expected to be named like '<project>-<branch>-<image>-<machine>-swu'."
                ),
            )
            p.add_argument(
                "--image",
                default="demo-image-base",
                help="Image name used to build artifact names and select the .swu inside each artifact (default: demo-image-base)",
            )
            p.add_argument(
                "--machine",
                default="jetson-orin-nano-devkit-nvme",
                help="Machine name used to build artifact names (default: jetson-orin-nano-devkit-nvme)",
            )

            p.add_argument(
                "--download-dir",
                default=str(Path.cwd() / "swupdate_artifacts"),
                help="Directory to download/extract artifacts into (default: ./swupdate_artifacts)",
            )
            p.add_argument(
                "--skip-download",
                action="store_true",
                help="Treat run_ref as a local directory and do not attempt to download from GitHub",
            )
            p.add_argument(
                "--download-only",
                action="store_true",
                help="Download/extract artifacts and resolve the update sequence, but do not touch the device",
            )
            p.add_argument(
                "--download-all",
                action="store_true",
                help="Download all artifacts from the run (default is to download only the ones needed for the selected project/releases)",
            )
            p.add_argument(
                "--reextract",
                action="store_true",
                help="Re-extract artifacts even if extracted directories already exist",
            )

            p.add_argument(
                "--github-token",
                default=None,
                help="GitHub token for Actions artifact download (default: env GITHUB_TOKEN or GH_TOKEN)",
            )
            p.add_argument(
                "--owner",
                default=None,
                help="GitHub owner/org (required only if run_ref is a bare run id)",
            )
            p.add_argument(
                "--repo",
                default=None,
                help="GitHub repo name (required only if run_ref is a bare run id)",
            )

            p.add_argument(
                "--start-release",
                default=self.RELEASE_ORDER[0],
                choices=list(self.RELEASE_ORDER),
                help="Start of the update sequence (default: kirkstone)",
            )
            p.add_argument(
                "--end-release",
                default=self.RELEASE_ORDER[-1],
                choices=list(self.RELEASE_ORDER),
                help="End of the update sequence (default: master)",
            )

            p.add_argument(
                "--remote-update-path",
                default="/tmp/swupdate.swu",
                help="Where to copy the update file on the target before running swupdate",
            )
            p.add_argument(
                "--swupdate-command",
                default="swupdate -v -i {remote}",
                help=(
                    "Command to run on the target to stage the update. '{remote}' will be replaced with --remote-update-path. "
                    "(default: 'swupdate -v -i {remote}')"
                ),
            )
            p.add_argument(
                "--no-verify-slot-change",
                action="store_true",
                help="Do not verify nvbootctrl slot changes/mounts after updates",
            )

            self.argparser = p
        return self.argparser

    def _load_dotenv_if_present(self) -> None:
        """Load a local .env file (if present) to populate environment variables.

        This is intentionally lightweight (no python-dotenv dependency). It only sets
        GITHUB_TOKEN / GH_TOKEN if they are not already set (or are set to an empty value)
        in the environment.
        """

        if getattr(self, "_dotenv_loaded", False):
            return
        self._dotenv_loaded = True
        self._dotenv_path_used = None
        self._dotenv_keys_loaded: List[str] = []

        candidates = [Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"]
        dotenv_path = next((p for p in candidates if p.exists() and p.is_file()), None)
        if dotenv_path is None:
            return
        self._dotenv_path_used = dotenv_path

        try:
            content = dotenv_path.read_text(encoding="utf-8")
        except Exception:
            # If we can't read it, just ignore it and fall back to normal env handling.
            return

        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            # allow: export KEY=VALUE
            if line.startswith("export "):
                line = line[len("export ") :].strip()

            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            # strip matching quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]

            if key in ("GITHUB_TOKEN", "GH_TOKEN") and value:
                existing = os.environ.get(key)
                if not existing:
                    os.environ[key] = value
                    self._dotenv_keys_loaded.append(key)

    def _auth_header_value(self, token: str) -> str:
        # GitHub classic PATs typically start with ghp_; fine-grained PATs start with github_pat_.
        # GitHub docs differ on schemes; accept both in a best-effort way.
        if token.startswith("github_pat_"):
            return f"Bearer {token}"
        return f"token {token}"

    def _print_github_token_diagnostics(self, token: Optional[str]) -> None:
        if getattr(self, "_printed_token_diagnostics", False):
            return
        self._printed_token_diagnostics = True

        args = self.get_args()
        dotenv_path = getattr(self, "_dotenv_path_used", None)
        dotenv_keys = getattr(self, "_dotenv_keys_loaded", [])

        if args.github_token:
            scheme = self._auth_header_value(args.github_token).split(" ", 1)[0]
            print(f"GitHub token source: --github-token (Authorization scheme: {scheme})")
            return

        env_gh = os.environ.get("GITHUB_TOKEN")
        if env_gh:
            scheme = self._auth_header_value(env_gh).split(" ", 1)[0]
            src = "environment variable GITHUB_TOKEN"
            if "GITHUB_TOKEN" in dotenv_keys and dotenv_path:
                src = f".env file at {dotenv_path} (GITHUB_TOKEN)"
            print(f"GitHub token source: {src} (Authorization scheme: {scheme})")
            return

        env_alt = os.environ.get("GH_TOKEN")
        if env_alt:
            scheme = self._auth_header_value(env_alt).split(" ", 1)[0]
            src = "environment variable GH_TOKEN"
            if "GH_TOKEN" in dotenv_keys and dotenv_path:
                src = f".env file at {dotenv_path} (GH_TOKEN)"
            print(f"GitHub token source: {src} (Authorization scheme: {scheme})")
            return

        searched = ["--github-token", "env:GITHUB_TOKEN", "env:GH_TOKEN", "./.env", "<script_dir>/.env"]
        print(f"GitHub token source: NOT FOUND (searched: {', '.join(searched)})")

    def _get_github_token(self) -> Optional[str]:
        self._load_dotenv_if_present()
        args = self.get_args()
        token = args.github_token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self._print_github_token_diagnostics(token)
        return token

    def _parse_run_ref(self, run_ref: str) -> Tuple[Optional[Path], Optional[GithubRunRef]]:
        p = Path(run_ref)
        if p.exists():
            return p, None

        # URL form: https://github.com/<owner>/<repo>/actions/runs/<id>
        m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/actions/runs/(\d+)", run_ref)
        if m:
            owner, repo, run_id_s = m.group(1), m.group(2), m.group(3)
            return None, GithubRunRef(owner=owner, repo=repo, run_id=int(run_id_s))

        # Bare numeric run id
        if run_ref.isdigit():
            args = self.get_args()
            if not args.owner or not args.repo:
                raise SystemExit("run_ref is a numeric run id; you must also pass --owner and --repo")
            return None, GithubRunRef(owner=args.owner, repo=args.repo, run_id=int(run_ref))

        raise SystemExit(f"run_ref is neither an existing path nor a recognized GitHub run URL/id: {run_ref}")

    def _http_get_json(self, url: str, token: Optional[str]) -> Dict:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "linux-test-scripts")
        if token:
            req.add_header("Authorization", self._auth_header_value(token))

        try:
            with urllib.request.urlopen(req) as resp:
                payload = resp.read().decode("utf-8")
                return json.loads(payload)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            msg = f"HTTP error {e.code} fetching {url}"
            if body:
                msg += f"\nResponse body: {body}"
            if e.code == 401:
                msg += (
                    "\n\n401 Unauthorized from GitHub API usually means: missing token, invalid token, "
                    "or the token does not have access to this repo/run. "
                    "For fine-grained PATs, ensure the token has Actions read permission for the repo."
                )
            raise RuntimeError(msg) from e

    class _StripAuthOnCrossHostRedirect(urllib.request.HTTPRedirectHandler):
        """Redirect handler that removes Authorization when redirecting to a different host.

        GitHub's artifact download endpoint commonly redirects to a signed URL on a
        different host (e.g. pipelines.actions.githubusercontent.com). Sending the GitHub
        Authorization header to that host can result in a 401 from the backing storage.
        """

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
            if new_req is None:
                return None

            try:
                old_host = urllib.parse.urlparse(req.full_url).hostname
                new_host = urllib.parse.urlparse(newurl).hostname
            except Exception:
                old_host = None
                new_host = None

            if old_host and new_host and old_host != new_host:
                # urllib stores headers in both headers and unredirected_hdrs depending on how they were set.
                if "Authorization" in new_req.headers:
                    del new_req.headers["Authorization"]
                if hasattr(new_req, "unredirected_hdrs") and "Authorization" in new_req.unredirected_hdrs:
                    del new_req.unredirected_hdrs["Authorization"]

            return new_req

    def _safe_url_for_logs(self, url: str) -> str:
        """Return a redacted URL suitable for logs (no query string)."""
        try:
            u = urllib.parse.urlparse(url)
            return urllib.parse.urlunparse((u.scheme, u.netloc, u.path, "", "", ""))
        except Exception:
            return url

    def _format_bytes(self, n: int) -> str:
        units = ["B", "KiB", "MiB", "GiB", "TiB"]
        f = float(n)
        for u in units:
            if f < 1024.0 or u == units[-1]:
                if u == "B":
                    return f"{int(f)} {u}"
                return f"{f:.1f} {u}"
            f /= 1024.0
        return f"{n} B"

    def _http_download_file(self, url: str, dest: Path, token: Optional[str]) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)

        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "linux-test-scripts")
        if token:
            req.add_header("Authorization", self._auth_header_value(token))

        # Use a redirect handler that strips Authorization on cross-host redirects.
        opener = urllib.request.build_opener(self._StripAuthOnCrossHostRedirect())

        try:
            with opener.open(req) as resp, open(dest, "wb") as f:
                final_url = getattr(resp, "geturl", lambda: url)()
                if final_url and final_url != url:
                    print(
                        "Artifact download redirect: "
                        f"{self._safe_url_for_logs(url)} -> {self._safe_url_for_logs(final_url)}"
                    )

                # Progress reporting (useful when artifacts are large and download takes a while)
                total_s = resp.headers.get("Content-Length") if hasattr(resp, "headers") else None
                total: Optional[int] = None
                if total_s:
                    try:
                        total = int(total_s)
                    except Exception:
                        total = None

                if total is not None:
                    print(
                        f"Downloading artifact bytes -> {dest} (total {self._format_bytes(total)})",
                        flush=True,
                    )
                else:
                    print(f"Downloading artifact bytes -> {dest} (total unknown)", flush=True)

                downloaded = 0
                last_print_t = time.time()
                last_print_bytes = 0
                chunk_size = 1024 * 1024  # 1 MiB

                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    now = time.time()
                    if (downloaded - last_print_bytes) >= (10 * 1024 * 1024) or (now - last_print_t) >= 5.0:
                        if total:
                            pct = (downloaded / total) * 100.0
                            print(
                                f"  downloaded {self._format_bytes(downloaded)} / {self._format_bytes(total)} ({pct:.1f}%)",
                                flush=True,
                            )
                        else:
                            print(f"  downloaded {self._format_bytes(downloaded)}", flush=True)
                        last_print_t = now
                        last_print_bytes = downloaded

                # Ensure we emit a final progress line.
                if total:
                    print(
                        f"  downloaded {self._format_bytes(downloaded)} / {self._format_bytes(total)} (100.0%)",
                        flush=True,
                    )
                else:
                    print(f"  downloaded {self._format_bytes(downloaded)}", flush=True)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            msg = f"HTTP error {e.code} downloading {self._safe_url_for_logs(url)}"
            if body:
                msg += f"\nResponse body: {body}"
            if e.code == 401:
                msg += (
                    "\n\n401 Unauthorized while downloading an artifact. "
                    "If the response is XML with InvalidAuthenticationInfo, it can indicate the GitHub Authorization header "
                    "was forwarded to the redirected storage host. This script now strips Authorization on cross-host redirects; "
                    "re-run to confirm."
                )
            raise RuntimeError(msg) from e

    def _list_run_artifacts(self, run: GithubRunRef, token: Optional[str]) -> List[Dict]:
        artifacts: List[Dict] = []
        page = 1
        while True:
            url = (
                f"https://api.github.com/repos/{run.owner}/{run.repo}/actions/runs/{run.run_id}/artifacts"
                f"?per_page=100&page={page}"
            )
            data = self._http_get_json(url, token)
            batch = data.get("artifacts", [])
            artifacts.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return artifacts

    def _artifact_name(self, project: str, branch: str, image: str, machine: str) -> str:
        # Dedicated SWU-only artifact name
        return f"{project}-{branch}-{image}-{machine}{self.SWU_ARTIFACT_SUFFIX}"

    def _artifact_needed(self, name: str, project: str, image: str, machine: str, releases: Iterable[str]) -> bool:
        for rel in releases:
            expected = self._artifact_name(project, rel, image, machine)
            if re.match(rf"^{re.escape(expected)}($|[-_].*)", name):
                return True
        return False

    def _download_and_extract_artifacts(
        self,
        run: GithubRunRef,
        dest_dir: Path,
        token: Optional[str],
        project: str,
        image: str,
        machine: str,
        releases: Sequence[str],
        download_all: bool,
        reextract: bool,
    ) -> List[LocalArtifact]:
        dest_dir.mkdir(parents=True, exist_ok=True)

        if token is None:
            raise RuntimeError(
                "No GitHub token available. Provide --github-token, set env GITHUB_TOKEN/GH_TOKEN, "
                "or create a local .env containing GITHUB_TOKEN=<token>."
            )

        artifacts = self._list_run_artifacts(run, token)
        if not artifacts:
            raise RuntimeError(f"No artifacts found for run {run.owner}/{run.repo}#{run.run_id}")

        selected = artifacts
        if not download_all:
            selected = [
                a
                for a in artifacts
                if self._artifact_needed(a.get("name", ""), project=project, image=image, machine=machine, releases=releases)
            ]

        if not selected:
            names = [a.get("name", "") for a in artifacts]
            raise RuntimeError(
                "No artifacts matched the selected artifact name pattern. "
                f"Project={project}, image={image}, machine={machine}, releases={list(releases)}\nAvailable artifacts: {names}"
            )

        out: List[LocalArtifact] = []
        for a in selected:
            name = a.get("name")
            dl_url = a.get("archive_download_url")
            if not name or not dl_url:
                continue

            zip_path = dest_dir / f"{name}.zip"
            extracted_dir = dest_dir / name

            if not zip_path.exists():
                print(f"Downloading artifact '{name}' -> {zip_path}")
                if token is None:
                    raise RuntimeError(
                        "No GitHub token available. Set --github-token or env GITHUB_TOKEN/GH_TOKEN to download artifacts."
                    )
                self._http_download_file(dl_url, zip_path, token)
            else:
                print(f"Using existing download {zip_path}")

            if extracted_dir.exists() and reextract:
                shutil.rmtree(extracted_dir)

            if not extracted_dir.exists():
                print(f"Extracting {zip_path} -> {extracted_dir}")
                extracted_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(extracted_dir)
            else:
                print(f"Using existing extracted dir {extracted_dir}")

            out.append(LocalArtifact(name=name, zip_path=zip_path, extracted_dir=extracted_dir))

        return out

    def _extract_local_zips_if_needed(self, artifacts_dir: Path, reextract: bool) -> List[LocalArtifact]:
        if not artifacts_dir.exists() or not artifacts_dir.is_dir():
            raise RuntimeError(f"Local artifacts dir does not exist or is not a directory: {artifacts_dir}")

        out: List[LocalArtifact] = []

        # Treat immediate subdirectories as potential artifacts.
        for child in sorted(artifacts_dir.iterdir()):
            if child.is_dir():
                out.append(LocalArtifact(name=child.name, zip_path=None, extracted_dir=child))

        # Also treat zip files in the dir as artifacts; extract next to the zip.
        for zip_path in sorted(artifacts_dir.glob("*.zip")):
            name = zip_path.stem
            extracted_dir = artifacts_dir / name
            if extracted_dir.exists() and reextract:
                shutil.rmtree(extracted_dir)
            if not extracted_dir.exists():
                print(f"Extracting local zip {zip_path} -> {extracted_dir}")
                extracted_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(extracted_dir)
            out.append(LocalArtifact(name=name, zip_path=zip_path, extracted_dir=extracted_dir))

        # Deduplicate by name (prefer zip-backed ones if both exist).
        by_name: Dict[str, LocalArtifact] = {}
        for a in out:
            if a.name in by_name:
                if by_name[a.name].zip_path is None and a.zip_path is not None:
                    by_name[a.name] = a
            else:
                by_name[a.name] = a

        return list(by_name.values())

    def _pick_artifact_for_release(
        self,
        artifacts: Sequence[LocalArtifact],
        project: str,
        release: str,
        image: str,
        machine: str,
    ) -> Optional[LocalArtifact]:
        # Primary expected naming:
        #   <project>-<branch>-<image>-<machine>-swu
        expected = self._artifact_name(project, release, image, machine)

        for a in artifacts:
            if a.name == expected:
                return a

        pat = re.compile(rf"^{re.escape(expected)}($|[-_].*)")
        candidates = [a for a in artifacts if pat.match(a.name)]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            # Prefer shortest (closest to expected) if there are suffix variants.
            return sorted(candidates, key=lambda x: len(x.name))[0]

        # Backwards-compatible fallbacks (older naming schemes):
        #   <project>-<branch>-<image>-<machine> (no -swu suffix)
        legacy_expected = f"{project}-{release}-{image}-{machine}"
        legacy_pat = re.compile(rf"^{re.escape(legacy_expected)}($|[-_].*)")
        legacy = [a for a in artifacts if legacy_pat.match(a.name)]
        if len(legacy) == 1:
            return legacy[0]
        if len(legacy) > 1:
            return sorted(legacy, key=lambda x: len(x.name))[0]

        #   <project>-<image>-<machine>-<branch>
        alt_expected = f"{project}-{image}-{machine}-{release}"
        alt_pat = re.compile(rf"^{re.escape(alt_expected)}($|[-_].*)")
        alt = [a for a in artifacts if alt_pat.match(a.name)]
        if len(alt) == 1:
            return alt[0]
        if len(alt) > 1:
            return sorted(alt, key=lambda x: len(x.name))[0]

        #   <project>-<branch>
        #   <project>-<branch>-<image>
        #   <project>-<branch>-<image>-<machine>
        fallback_pat = re.compile(rf"^{re.escape(project)}-{re.escape(release)}($|[-_].*)")
        fallback = [a for a in artifacts if fallback_pat.match(a.name)]
        if len(fallback) == 1:
            return fallback[0]
        if len(fallback) > 1:
            # Try to disambiguate using image/machine components.
            img = [a for a in fallback if image in a.name]
            if len(img) == 1:
                return img[0]
            if len(img) > 1:
                mach = [a for a in img if machine in a.name]
                if len(mach) == 1:
                    return mach[0]

            names = [c.name for c in fallback]
            raise RuntimeError(f"Multiple artifacts match {project}/{release}: {names}")

        return None

    def _select_swu_file(self, artifact: LocalArtifact, image: str) -> Path:
        swus = sorted(artifact.extracted_dir.glob("**/*.swu"))
        if not swus:
            raise RuntimeError(f"No .swu files found inside artifact '{artifact.name}' at {artifact.extracted_dir}")

        if len(swus) == 1:
            return swus[0]

        # Prefer files that contain the image name.
        matches = [p for p in swus if image in p.name]
        if len(matches) == 1:
            return matches[0]

        # Prefer an exact-ish name if present.
        for p in swus:
            if p.name == f"{image}.swu" or p.name.startswith(f"{image}-"):
                return p

        raise RuntimeError(
            f"Multiple .swu files found in artifact '{artifact.name}', and none could be uniquely selected for image '{image}'. "
            f"Candidates: {[p.relative_to(artifact.extracted_dir) for p in swus]}"
        )

    def _resolve_sequence(
        self,
        artifacts: Sequence[LocalArtifact],
        project: str,
        image: str,
        machine: str,
        start: str,
        end: str,
    ) -> List[Tuple[str, LocalArtifact]]:
        if start not in self.RELEASE_ORDER or end not in self.RELEASE_ORDER:
            raise RuntimeError(f"Invalid start/end release. Choices: {list(self.RELEASE_ORDER)}")

        start_i = list(self.RELEASE_ORDER).index(start)
        end_i = list(self.RELEASE_ORDER).index(end)
        if end_i < start_i:
            raise RuntimeError(f"end-release '{end}' must not come before start-release '{start}'")

        sequence: List[Tuple[str, LocalArtifact]] = []
        for rel in self.RELEASE_ORDER[start_i : end_i + 1]:
            a = self._pick_artifact_for_release(
                artifacts,
                project=project,
                release=rel,
                image=image,
                machine=machine,
            )
            if a is None:
                # Only kirkstone and master are required; others are optional.
                if rel in ("kirkstone", "master"):
                    available = sorted([x.name for x in artifacts])
                    raise RuntimeError(
                        f"Missing required artifact for release '{rel}'. Expected '{self._artifact_name(project, rel, image, machine)}'. Available: {available}"
                    )
                print(f"Skipping missing optional release artifact: {self._artifact_name(project, rel, image, machine)}")
                continue
            sequence.append((rel, a))

        return sequence

    def _transfer_file_with_retries(self, local_path: Path, remote_path: str, attempts: int = 3) -> None:
        last_exc: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                print(f"Copying {local_path} -> {remote_path} (attempt {attempt}/{attempts})")
                self.get_connection().put(local=str(local_path), remote=remote_path)
                return
            except Exception as e:
                last_exc = e
                time.sleep(2)

        raise RuntimeError(f"Failed to transfer {local_path} after {attempts} attempts") from last_exc

    def _apply_update(self, swu_path: Path, remote_path: str, swupdate_command: str, verify_slot_change: bool) -> None:
        self._transfer_file_with_retries(swu_path, remote_path)

        cmd = swupdate_command.format(remote=remote_path)
        print(f"Running on target: {cmd}")
        self.get_connection().run(cmd)

        if verify_slot_change:
            self.verify_update(check_capsule_success=False)
        else:
            self.reboot()

    def do_test(self):
        args = self.get_args()

        local_dir, run = self._parse_run_ref(args.run_ref)

        artifacts_dir: Path
        artifacts: List[LocalArtifact]

        if args.skip_download:
            if local_dir is None:
                raise SystemExit("--skip-download requires run_ref to be an existing local directory")
            artifacts_dir = local_dir
            print(f"Using local artifacts directory: {artifacts_dir}")
            artifacts = self._extract_local_zips_if_needed(artifacts_dir, reextract=args.reextract)
        else:
            if run is None:
                if local_dir is None:
                    raise SystemExit("run_ref could not be parsed")
                artifacts_dir = local_dir
                print(f"Using local artifacts directory: {artifacts_dir}")
                artifacts = self._extract_local_zips_if_needed(artifacts_dir, reextract=args.reextract)
            else:
                artifacts_dir = Path(args.download_dir) / f"{run.owner}-{run.repo}-run-{run.run_id}"
                print(f"Downloading artifacts into directory: {artifacts_dir}")
                token = self._get_github_token()
                artifacts = self._download_and_extract_artifacts(
                    run=run,
                    dest_dir=artifacts_dir,
                    token=token,
                    project=args.project,
                    image=args.image,
                    machine=args.machine,
                    releases=self.RELEASE_ORDER,
                    download_all=args.download_all,
                    reextract=args.reextract,
                )

        sequence = self._resolve_sequence(
            artifacts,
            project=args.project,
            image=args.image,
            machine=args.machine,
            start=args.start_release,
            end=args.end_release,
        )

        print("Resolved update sequence:")
        for rel, a in sequence:
            print(f"  - {rel}: {a.name} ({a.extracted_dir})")

        swu_by_release: List[Tuple[str, Path]] = []
        for rel, a in sequence:
            swu = self._select_swu_file(a, args.image)
            swu_by_release.append((rel, swu))
            print(f"Selected .swu for {rel}: {swu}")

        if args.download_only:
            print("--download-only specified; exiting before device interaction")
            return 0

        verify_slot_change = not args.no_verify_slot_change

        for rel, swu in swu_by_release:
            print(f"\n=== Applying update for release '{rel}' using {swu} ===")
            self._apply_update(
                swu_path=swu,
                remote_path=args.remote_update_path,
                swupdate_command=args.swupdate_command,
                verify_slot_change=verify_slot_change,
            )
            print(f"=== Completed update for release '{rel}' ===")

        print("Sequence completed successfully")
        return 0


if __name__ == "__main__":
    test = SwupdateTestSequence()
    sys.exit(test.do_test())
