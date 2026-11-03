#!/usr/bin/env python
"""Watchable end-to-end run of the hub/spoke sync through MongoDB, driven with Playwright.

The script runs in the foreground, opens one visible browser window per Nautobot instance and narrates every step
on the terminal. It drives the two Docker Compose stacks of the development environment:

- the default stack (`invoke start`) plays a spoke named `SPOKE-Dev` on http://localhost:8080
- the second stack (`invoke -f invoke.hub.yml start`) plays the hub on http://localhost:8081

Both share the MongoDB of the default stack. The sequence mirrors the architecture diagram:

1. The hub owns reference data (created through its REST API if missing) and loads it into MongoDB with
   "Nautobot to MongoDB".
2. The spoke runs "MongoDB to Nautobot": the SSoT hub adapter syncs the hub-owned models into the DiffSync store and
   CRUD operations apply them to the spoke.
3. The spoke owns a location tree, a rack, two devices with interfaces, a VLAN, an IP address and a cable (created
   through its REST API) and loads them into MongoDB with "Nautobot to MongoDB".
4. The hub runs "MongoDB to Nautobot" for the spoke's partition: the SSoT spoke adapter syncs the spoke-owned models in,
   stamps each with the spoke as owner, and the device and cable appear on the hub. The registry on the hub now lists
   both instances from the heartbeats their jobs wrote.
5. The spoke deletes the cable and publishes again; the hub pulls once more. The cable disappears from the hub through
   the tombstone the spoke wrote, while a location the hub created itself is left untouched.

Playwright is a development dependency of the project. Run the script from the repository root, on the host:

    poetry run playwright install chromium   # once, downloads the browser
    invoke e2e                               # or: poetry run python development/bin/e2e_hub_spoke.py

Useful options: `--no-start` when both stacks are already up, `--keep-mongo` to leave the collection as it is,
`--keep-spoke-objects` to keep whatever devices and locations the spoke already has, `--slow-mo 500` to slow the
browser down, `--no-hold` to close the browsers as soon as the run finishes, and `--fresh` to destroy both stacks
(including their databases) before starting.

Per-object sync errors are recorded by DiffSync in the SSoT sync log, not in the job log, so the script reads that
log after every job and fails the run if any object could not be synced.

!!! warning
    The run changes data on both instances: the shared collection is emptied, the spoke's devices and locations are
    removed and recreated, "MongoDB to Nautobot" on the spoke replaces the spoke's reference data with what the hub
    published, and "MongoDB to Nautobot" on the hub replaces the hub's locations and devices with what the spokes
    published. Only point this script at the development stacks.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

from playwright.sync_api import Browser, Page, Playwright, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SPOKE_URL = "http://localhost:8080"
HUB_URL = "http://localhost:8081"
SPOKE_INSTANCE = "SPOKE-Dev"
MONGO_CONTAINER = "nautobot-ssot-mongo-mongo-1"
SOURCE_JOB = "nautobot_ssot_mongo.jobs.MongoDataSource"  # MongoDB to Nautobot
TARGET_JOB = "nautobot_ssot_mongo.jobs.MongoDataTarget"  # Nautobot to MongoDB
JOB_TIMEOUT = 300


def log(step, message):
    """Print one narrated line."""
    print(f"[{datetime.now():%H:%M:%S}] [{step}] {message}", flush=True)


def run(command, env=None):
    """Run a shell command in the foreground, streaming its output."""
    log("shell", command)
    subprocess.run(command, shell=True, check=True, cwd=REPO_ROOT, env={**os.environ, **(env or {})})  # noqa: S602


def wait_http(url, timeout):
    """Block until `url` answers 200."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(3)
    raise TimeoutError(f"{url} did not answer within {timeout}s")


class Instance:
    """One Nautobot instance: a visible browser window plus REST calls made with the browser's session."""

    def __init__(self, name, base_url, browser: Browser, username, password):  # pylint: disable=too-many-arguments
        """Open a fresh browser context (own cookies) for one instance."""
        self.name = name
        self.base_url = base_url
        self.username = username
        self.password = password
        self.context = browser.new_context(viewport={"width": 1280, "height": 860})
        self.page: Page = self.context.new_page()

    # -- browser -------------------------------------------------------------------------------------------------

    def login(self):
        """Log in through the login form so that both the page and the REST calls share one session."""
        log(self.name, f"Logging in to {self.base_url} as {self.username}")
        self.page.goto(f"{self.base_url}/login/")
        self.page.fill("#id_username", self.username)
        self.page.fill("#id_password", self.password)
        self.page.click("button[type=submit]")
        self.page.wait_for_url(f"{self.base_url}/**")
        if "/login/" in self.page.url:
            raise RuntimeError(f"{self.name}: login failed for {self.username}")

    def show(self, path):
        """Navigate the browser window to a path of this instance."""
        self.page.goto(f"{self.base_url}{path}")

    # -- REST API through the browser session ----------------------------------------------------------------------

    def _headers(self):
        csrf = next(cookie["value"] for cookie in self.context.cookies() if cookie["name"] == "csrftoken")
        return {"X-CSRFToken": csrf, "Referer": f"{self.base_url}/", "Accept": "application/json"}

    def api(self, method, path, params=None, payload=None):
        """Call the REST API with the browser session and return the decoded JSON body."""
        response = self.context.request.fetch(
            f"{self.base_url}/api{path}",
            method=method,
            params=params,
            data=json.dumps(payload) if payload is not None else None,
            headers={**self._headers(), "Content-Type": "application/json"},
        )
        if not response.ok:
            raise RuntimeError(f"{self.name}: {method} {path} -> {response.status} {response.text()[:500]}")
        return response.json() if response.status != 204 else None

    def get_one(self, path, **params):
        """Return exactly one object matching the filter parameters."""
        results = self.api("GET", path, params={**params, "depth": 0})["results"]
        if len(results) != 1:
            raise RuntimeError(f"{self.name}: expected one object at {path} {params}, found {len(results)}")
        return results[0]

    def get_or_create(self, path, lookup, payload):
        """Return the object matching `lookup`, creating it from `payload` when it does not exist."""
        results = self.api("GET", path, params={**lookup, "depth": 0})["results"]
        if results:
            log(self.name, f"{path} {lookup} already exists")
            return results[0]
        created = self.api("POST", path, payload=payload)
        log(self.name, f"created {path} {lookup}")
        return created

    # -- jobs ----------------------------------------------------------------------------------------------------

    def enable_jobs(self):
        """Enable both SSoT jobs of this app; jobs are disabled by default."""
        for job in self.api("GET", "/extras/jobs/", params={"module_name": "nautobot_ssot_mongo.jobs"})["results"]:
            if not job["enabled"]:
                self.api("PATCH", f"/extras/jobs/{job['id']}/", payload={"enabled": True})
                log(self.name, f"enabled job '{job['name']}'")

    def run_job(self, class_path, nautobot_instances=None, dryrun=False):
        """Submit a job from its run form in the browser and wait for the result in the job result view.

        "MongoDB to Nautobot" carries a `Nautobot instances` multi-select whose choices come from the collection when
        the form renders, so a partition only shows up after the other role published to it.
        """
        label = "MongoDB to Nautobot" if class_path == SOURCE_JOB else "Nautobot to MongoDB"
        log(self.name, f"Running '{label}'{' (dry run)' if dryrun else ''} from the UI")
        self.page.goto(f"{self.base_url}/extras/jobs/{class_path}/run/")
        self.page.set_checked("#id_dryrun", dryrun)  # the schedule type defaults to "immediately"
        if nautobot_instances:
            # select2 hides the native <select>; force= lets Playwright drive it anyway.
            self.page.locator("#id_nautobot_instances").select_option(nautobot_instances, force=True)
        self.page.click("#id__run")
        try:
            self.page.wait_for_url("**/extras/job-results/**")
        except PlaywrightTimeoutError as error:
            errors = " | ".join(
                text.strip() for text in self.page.locator(".alert, .invalid-feedback").all_inner_texts()
            )
            raise RuntimeError(f"{self.name}: submitting the job form stayed on {self.page.url}: {errors}") from error
        job_result_id = self.page.url.rstrip("/").split("/")[-1]
        status = self._wait_job(job_result_id)
        log(self.name, f"'{label}' finished with status {status}")
        for entry in self.api("GET", "/extras/job-logs/", params={"job_result": job_result_id, "depth": 0})["results"]:
            if entry["log_level"] != "debug":
                log(self.name, f"  {entry['log_level']:<8} {entry['message']}")
        if status != "SUCCESS":
            raise RuntimeError(f"{self.name}: job {class_path} ended with status {status}")
        self._check_sync_log(job_result_id)
        return job_result_id

    def _check_sync_log(self, job_result_id):
        """Fail on per-object errors, which DiffSync records in the SSoT sync log rather than in the job log."""
        sync = self.get_one("/plugins/ssot/history/", job_result=job_result_id)
        problems = [
            entry
            for entry in self.api("GET", "/plugins/ssot/logs/", params={"sync": sync["id"], "depth": 0})["results"]
            if entry["status"] in ("error", "failure")
        ]
        for entry in problems:
            log(self.name, f"  SYNC {entry['status']:<7} {entry['action']} {entry['object_repr']}: {entry['message']}")
        if problems:
            raise RuntimeError(f"{self.name}: {len(problems)} object(s) failed to sync, see the SSoT sync log")

    def _wait_job(self, job_result_id):
        deadline = time.monotonic() + JOB_TIMEOUT
        while time.monotonic() < deadline:
            status = self.api("GET", f"/extras/job-results/{job_result_id}/", params={"depth": 0})["status"]["value"]
            if status in ("SUCCESS", "FAILURE", "REVOKED"):
                return status
            time.sleep(2)
        raise TimeoutError(f"{self.name}: job result {job_result_id} did not finish within {JOB_TIMEOUT}s")


# -- scenario --------------------------------------------------------------------------------------------------


def ensure_hub_reference_data(hub: Instance):
    """Create the reference data the hub owns, if it is not there yet."""
    log(hub.name, "Ensuring the hub owns the reference data the spoke device will reference")
    cisco = hub.get_or_create("/dcim/manufacturers/", {"name": "Cisco"}, {"name": "Cisco"})
    hub.get_or_create(
        "/dcim/platforms/",
        {"name": "Cisco IOS"},
        {"name": "Cisco IOS", "manufacturer": cisco["id"], "network_driver": "cisco_ios", "napalm_driver": "ios"},
    )
    hub.get_or_create(
        "/dcim/device-types/",
        {"model": "C9300-48P"},
        {"manufacturer": cisco["id"], "model": "C9300-48P", "part_number": "C9300-48P", "u_height": 1},
    )
    region = hub.get_or_create("/dcim/location-types/", {"name": "Region"}, {"name": "Region", "nestable": True})
    hub.get_or_create(
        "/dcim/location-types/",
        {"name": "Site"},
        {"name": "Site", "parent": region["id"], "content_types": ["dcim.device"]},
    )
    hub.get_or_create(
        "/extras/roles/",
        {"name": "Network Device"},
        {"name": "Network Device", "color": "ff9800", "content_types": ["dcim.device"]},
    )
    hub.show("/dcim/device-types/")


def reset_spoke_objects(spoke: Instance):
    """Remove the spoke's inventory so that it publishes exactly the objects of this scenario."""
    log(spoke.name, "Removing the spoke's existing inventory")
    for path in (
        "/dcim/cables/",
        "/dcim/devices/",
        "/dcim/racks/",
        "/dcim/rack-groups/",
        "/ipam/ip-addresses/",
        "/ipam/vlans/",
        "/dcim/locations/",
    ):
        for obj in spoke.api("GET", path, params={"depth": 0, "limit": 1000})["results"]:
            response = spoke.context.request.delete(f"{spoke.base_url}/api{path}{obj['id']}/", headers=spoke._headers())  # pylint: disable=protected-access
            if response.ok:
                log(spoke.name, f"deleted {path}{obj.get('name') or obj.get('display') or obj['id']}")
            elif response.status != 404:  # already removed by a cascading delete of its parent
                raise RuntimeError(
                    f"{spoke.name}: DELETE {path}{obj['id']}/ -> {response.status} {response.text()[:300]}"
                )


def create_spoke_objects(spoke: Instance):  # pylint: disable=too-many-locals
    """Create the inventory the spoke owns, using the catalog it imported from the hub."""
    log(spoke.name, "Creating the spoke-owned location tree, rack, devices, interfaces, VLAN, IP address and cable")
    active = spoke.get_one("/extras/statuses/", name="Active")
    region_type = spoke.get_one("/dcim/location-types/", name="Region")
    site_type = spoke.get_one("/dcim/location-types/", name="Site")
    region = spoke.get_or_create(
        "/dcim/locations/",
        {"name": "US-South"},
        {"name": "US-South", "location_type": region_type["id"], "status": active["id"]},
    )
    site = spoke.get_or_create(
        "/dcim/locations/",
        {"name": "Austin"},
        {
            "name": "Austin",
            "location_type": site_type["id"],
            "parent": region["id"],
            "status": active["id"],
            "facility": "AUS1",
        },
    )
    group = spoke.get_or_create("/dcim/rack-groups/", {"name": "Row A"}, {"name": "Row A", "location": site["id"]})
    spoke.get_or_create(
        "/dcim/racks/",
        {"name": "A1"},
        {"name": "A1", "location": site["id"], "rack_group": group["id"], "status": active["id"], "u_height": 42},
    )
    device_payload = {
        "location": site["id"],
        "device_type": spoke.get_one("/dcim/device-types/", model="C9300-48P")["id"],
        "role": spoke.get_one("/extras/roles/", name="Network Device")["id"],
        "platform": spoke.get_one("/dcim/platforms/", name="Cisco IOS")["id"],
        "status": active["id"],
    }
    device = spoke.get_or_create(
        "/dcim/devices/", {"name": "aus-core-01"}, {"name": "aus-core-01", "serial": "JPE00000001", **device_payload}
    )
    edge = spoke.get_or_create("/dcim/devices/", {"name": "aus-edge-01"}, {"name": "aus-edge-01", **device_payload})
    interfaces = {}
    for dev in (device, edge):
        interfaces[dev["name"]] = spoke.get_or_create(
            "/dcim/interfaces/",
            {"device": dev["id"], "name": "Ethernet1"},
            {"device": dev["id"], "name": "Ethernet1", "type": "1000base-t", "status": active["id"]},
        )
    vlan = spoke.get_or_create(
        "/ipam/vlans/", {"vid": 100, "location": site["id"]}, {"vid": 100, "name": "Users", "status": active["id"]}
    )
    spoke.api("PATCH", f"/ipam/vlans/{vlan['id']}/", payload={"locations": [site["id"]]})
    spoke.get_or_create(
        "/ipam/ip-addresses/",
        {"address": "10.0.0.1/32"},
        {
            "address": "10.0.0.1/32",
            "namespace": spoke.get_one("/ipam/namespaces/", name="Global")["id"],
            "status": active["id"],
            "dns_name": "aus-core-01.example.com",
        },
    )
    spoke.get_or_create(
        "/dcim/cables/",
        {"termination_a_id": interfaces["aus-core-01"]["id"]},
        {
            "termination_a_type": "dcim.interface",
            "termination_a_id": interfaces["aus-core-01"]["id"],
            "termination_b_type": "dcim.interface",
            "termination_b_id": interfaces["aus-edge-01"]["id"],
            "status": spoke.get_one("/extras/statuses/", name="Connected")["id"],
            "label": "core-edge",
        },
    )
    spoke.show(f"/dcim/devices/{device['id']}/")


def verify_hub_has_the_device(hub: Instance):
    """Check through the hub's API that the spoke's inventory arrived, and show the device in the browser."""
    device = hub.get_one("/dcim/devices/", name="aus-core-01")
    detail = hub.api("GET", f"/dcim/devices/{device['id']}/", params={"depth": 1})
    location = detail["location"]["name"]
    parent = hub.api("GET", f"/dcim/locations/{detail['location']['id']}/", params={"depth": 1})["parent"]["name"]
    log(hub.name, f"Device aus-core-01 is on the hub at {parent} / {location}")
    hub.show(f"/dcim/devices/{device['id']}/")
    if location != "Austin" or parent != "US-South":
        raise RuntimeError("the device on the hub is not under US-South / Austin")
    cables = hub.api("GET", "/dcim/cables/", params={"label": "core-edge", "depth": 0})["results"]
    vlans = hub.api("GET", "/ipam/vlans/", params={"vid": 100, "depth": 0})["results"]
    owner = hub.api("GET", "/extras/object-metadata/", params={"assigned_object_id": device["id"], "depth": 0})
    log(
        hub.name,
        f"cables: {len(cables)}, VLAN 100: {len(vlans)}, owner metadata: {[m['value'] for m in owner['results']]}",
    )
    if len(cables) != 1 or len(vlans) != 1:
        raise RuntimeError("the spoke's cable or VLAN did not arrive on the hub")


def verify_registry(hub: Instance):
    """Check that the hub's registry lists both instances from their heartbeats, and show it in the browser."""
    rows = {row["name"]: row for row in hub.api("GET", "/plugins/ssot-mongo/federated-instances/")["results"]}
    log(hub.name, f"Federated instances: {', '.join(f'{name} ({row['role']})' for name, row in rows.items())}")
    hub.show("/plugins/ssot-mongo/federated-instances/")
    spoke_row = rows.get(SPOKE_INSTANCE)
    if spoke_row is None or spoke_row["role"] != "SPOKE" or not spoke_row["last_publish"]:
        raise RuntimeError(f"the registry on the hub does not list {SPOKE_INSTANCE} with a last publish time")
    if spoke_row["document_counts"].get("dcim.device", 0) < 2:
        raise RuntimeError(f"the registry shows fewer than two devices published by {SPOKE_INSTANCE}")
    hub_rows = [row for row in rows.values() if row["role"] == "HUB"]
    if len(hub_rows) != 1 or not hub_rows[0]["last_pull"]:
        raise RuntimeError("the registry on the hub does not list the hub itself with a last pull time")


def ensure_hub_local_location(hub: Instance):
    """Create a location on the hub that no spoke owns; the scoped pull must leave it alone."""
    active = hub.get_one("/extras/statuses/", name="Active")
    region_type = hub.get_one("/dcim/location-types/", name="Region")
    return hub.get_or_create(
        "/dcim/locations/",
        {"name": "HUB-Only"},
        {"name": "HUB-Only", "location_type": region_type["id"], "status": active["id"]},
    )


def retire_the_cable(spoke: Instance, hub: Instance):
    """Delete the cable on the spoke, publish, pull on the hub and check the tombstone removed it there too."""
    cable = spoke.get_one("/dcim/cables/", label="core-edge")
    spoke.context.request.delete(f"{spoke.base_url}/api/dcim/cables/{cable['id']}/", headers=spoke._headers())  # pylint: disable=protected-access
    log(spoke.name, "deleted the core-edge cable")
    spoke.run_job(TARGET_JOB)
    hub.run_job(SOURCE_JOB, nautobot_instances=[SPOKE_INSTANCE])
    if hub.api("GET", "/dcim/cables/", params={"label": "core-edge", "depth": 0})["results"]:
        raise RuntimeError("the retired cable is still on the hub")
    if not hub.api("GET", "/dcim/locations/", params={"name": "HUB-Only", "depth": 0})["results"]:
        raise RuntimeError("the hub's own location was removed by the pull")
    log(hub.name, "the retired cable is gone and the hub-only location survived")


def reset_mongo():
    """Delete every document in the shared collection through the mongo container."""
    log("mongo", "Emptying the shared collection so the run starts from a known state")
    run(
        f"docker exec {MONGO_CONTAINER} mongosh --quiet --eval "
        "\"db.getSiblingDB('nautobot_sync').objects.deleteMany({})\""
    )


def start_stacks(fresh):
    """Bring up the default (spoke) stack and the hub stack with invoke, optionally destroying them first."""
    if fresh:
        answer = input("--fresh destroys BOTH stacks including their databases. Type 'yes' to continue: ")
        if answer.strip().lower() != "yes":
            sys.exit("aborted")
        run("invoke -f invoke.hub.yml destroy")
        run("invoke destroy")
    log("stack", "Starting the default stack (SPOKE-Dev, http://localhost:8080)")
    run("invoke start")
    log("stack", "Starting the hub stack (hub, http://localhost:8081)")
    run("invoke -f invoke.hub.yml start")


def launch_browser(playwright: Playwright, slow_mo, position):
    """Launch one visible Chromium window at the given screen position."""
    return playwright.chromium.launch(
        headless=False,
        slow_mo=slow_mo,
        args=[f"--window-position={position}", "--window-size=1300,940"],
    )


def main(argv=None):
    """Run the whole hub -> spoke -> hub loop and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-start", action="store_true", help="assume both stacks are already running")
    parser.add_argument("--fresh", action="store_true", help="destroy both stacks (and their data) before starting")
    parser.add_argument("--keep-mongo", action="store_true", help="do not empty the shared MongoDB collection first")
    parser.add_argument(
        "--keep-spoke-objects", action="store_true", help="do not remove the spoke's existing devices and locations"
    )
    parser.add_argument("--slow-mo", type=int, default=250, help="milliseconds to pause between browser actions")
    parser.add_argument("--no-hold", action="store_true", help="close the browsers as soon as the run finishes")
    parser.add_argument("--username", default=os.getenv("NAUTOBOT_SUPERUSER_NAME", "admin"))
    parser.add_argument("--password", default=os.getenv("NAUTOBOT_SUPERUSER_PASSWORD", "admin"))
    args = parser.parse_args(argv)

    if not args.no_start:
        start_stacks(args.fresh)
    log("stack", "Waiting for both instances to answer")
    wait_http(f"{SPOKE_URL}/health/", 600)
    wait_http(f"{HUB_URL}/health/", 600)
    if not args.keep_mongo:
        reset_mongo()

    with sync_playwright() as playwright:
        hub_browser = launch_browser(playwright, args.slow_mo, "0,0")
        spoke_browser = launch_browser(playwright, args.slow_mo, "1320,0")
        hub = Instance("HUB", HUB_URL, hub_browser, args.username, args.password)
        spoke = Instance("SPOKE-Dev", SPOKE_URL, spoke_browser, args.username, args.password)
        try:
            hub.login()
            spoke.login()
            hub.enable_jobs()
            spoke.enable_jobs()

            log("step 1", "hub loads the catalog it owns into MongoDB")
            ensure_hub_reference_data(hub)
            ensure_hub_local_location(hub)
            hub.run_job(TARGET_JOB)

            log("step 2", "spoke syncs the hub-owned reference data in from MongoDB")
            if not args.keep_spoke_objects:
                # Leftover spoke objects may reference tenants or roles the hub does not own, which would block the
                # reconciliation of the reference data; clear them first so the spoke mirrors the hub cleanly.
                reset_spoke_objects(spoke)
            spoke.run_job(SOURCE_JOB)

            log("step 3", "spoke loads the inventory it owns into MongoDB")
            create_spoke_objects(spoke)
            spoke.run_job(TARGET_JOB)

            log("step 4", "hub syncs the spoke-owned inventory in from MongoDB")
            hub.run_job(SOURCE_JOB, nautobot_instances=[SPOKE_INSTANCE])
            verify_hub_has_the_device(hub)
            verify_registry(hub)

            log("step 5", "spoke retires the cable; the tombstone removes it from the hub")
            retire_the_cable(spoke, hub)

            spoke.show("/plugins/ssot/")
            log("done", "Full hub -> spoke -> hub loop completed, including a tombstoned delete")
            if not args.no_hold:
                input("Press Enter to close the browsers... ")
        finally:
            hub_browser.close()
            spoke_browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
