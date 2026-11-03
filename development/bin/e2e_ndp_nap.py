#!/usr/bin/env python
"""Watchable end-to-end run of the NDP/NAP sync through MongoDB, driven with Playwright.

The script runs in the foreground, opens one visible browser window per Nautobot instance and narrates every step
on the terminal. It drives the two Docker Compose stacks of the development environment:

- the default stack (`invoke start`) plays a NAP named `NAP-Dev` on http://localhost:8080
- the second stack (`invoke -f invoke.ndp.yml start`) plays the NDP on http://localhost:8081

Both share the MongoDB of the default stack. The sequence mirrors the architecture diagram:

1. The NDP owns reference data (created through its REST API if missing) and loads it into MongoDB with
   "Nautobot to MongoDB".
2. The NAP runs "MongoDB to Nautobot": the SSoT NDP adapter syncs the NDP-owned models into the DiffSync store and
   CRUD operations apply them to the NAP.
3. The NAP owns a location tree, a rack, two devices with interfaces, a VLAN, an IP address and a cable (created
   through its REST API) and loads them into MongoDB with "Nautobot to MongoDB".
4. The NDP runs "MongoDB to Nautobot" for the NAP's partition: the SSoT NAP adapter syncs the NAP-owned models in,
   stamps each with the NAP as owner, and the device and cable appear on the NDP.
5. The NAP deletes the cable and publishes again; the NDP pulls once more. The cable disappears from the NDP through
   the tombstone the NAP wrote, while a location the NDP created itself is left untouched.

Playwright is a development dependency of the project. Run the script from the repository root, on the host:

    poetry run playwright install chromium   # once, downloads the browser
    invoke e2e                               # or: poetry run python development/bin/e2e_ndp_nap.py

Useful options: `--no-start` when both stacks are already up, `--keep-mongo` to leave the collection as it is,
`--keep-nap-objects` to keep whatever devices and locations the NAP already has, `--slow-mo 500` to slow the
browser down, `--no-hold` to close the browsers as soon as the run finishes, and `--fresh` to destroy both stacks
(including their databases) before starting.

Per-object sync errors are recorded by DiffSync in the SSoT sync log, not in the job log, so the script reads that
log after every job and fails the run if any object could not be synced.

!!! warning
    The run changes data on both instances: the shared collection is emptied, the NAP's devices and locations are
    removed and recreated, "MongoDB to Nautobot" on the NAP replaces the NAP's reference data with what the NDP
    published, and "MongoDB to Nautobot" on the NDP replaces the NDP's locations and devices with what the NAPs
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
NAP_URL = "http://localhost:8080"
NDP_URL = "http://localhost:8081"
NAP_INSTANCE = "NAP-Dev"
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


def ensure_ndp_reference_data(ndp: Instance):
    """Create the reference data the NDP owns, if it is not there yet."""
    log(ndp.name, "Ensuring the NDP owns the reference data the NAP device will reference")
    cisco = ndp.get_or_create("/dcim/manufacturers/", {"name": "Cisco"}, {"name": "Cisco"})
    ndp.get_or_create(
        "/dcim/platforms/",
        {"name": "Cisco IOS"},
        {"name": "Cisco IOS", "manufacturer": cisco["id"], "network_driver": "cisco_ios", "napalm_driver": "ios"},
    )
    ndp.get_or_create(
        "/dcim/device-types/",
        {"model": "C9300-48P"},
        {"manufacturer": cisco["id"], "model": "C9300-48P", "part_number": "C9300-48P", "u_height": 1},
    )
    region = ndp.get_or_create("/dcim/location-types/", {"name": "Region"}, {"name": "Region", "nestable": True})
    ndp.get_or_create(
        "/dcim/location-types/",
        {"name": "Site"},
        {"name": "Site", "parent": region["id"], "content_types": ["dcim.device"]},
    )
    ndp.get_or_create(
        "/extras/roles/",
        {"name": "Network Device"},
        {"name": "Network Device", "color": "ff9800", "content_types": ["dcim.device"]},
    )
    ndp.show("/dcim/device-types/")


def reset_nap_objects(nap: Instance):
    """Remove the NAP's inventory so that it publishes exactly the objects of this scenario."""
    log(nap.name, "Removing the NAP's existing inventory")
    for path in (
        "/dcim/cables/",
        "/dcim/devices/",
        "/dcim/racks/",
        "/dcim/rack-groups/",
        "/ipam/ip-addresses/",
        "/ipam/vlans/",
        "/dcim/locations/",
    ):
        for obj in nap.api("GET", path, params={"depth": 0, "limit": 1000})["results"]:
            response = nap.context.request.delete(f"{nap.base_url}/api{path}{obj['id']}/", headers=nap._headers())  # pylint: disable=protected-access
            if response.ok:
                log(nap.name, f"deleted {path}{obj.get('name') or obj.get('display') or obj['id']}")
            elif response.status != 404:  # already removed by a cascading delete of its parent
                raise RuntimeError(
                    f"{nap.name}: DELETE {path}{obj['id']}/ -> {response.status} {response.text()[:300]}"
                )


def create_nap_objects(nap: Instance):  # pylint: disable=too-many-locals
    """Create the inventory the NAP owns, using the catalog it imported from the NDP."""
    log(nap.name, "Creating the NAP-owned location tree, rack, devices, interfaces, VLAN, IP address and cable")
    active = nap.get_one("/extras/statuses/", name="Active")
    region_type = nap.get_one("/dcim/location-types/", name="Region")
    site_type = nap.get_one("/dcim/location-types/", name="Site")
    region = nap.get_or_create(
        "/dcim/locations/",
        {"name": "US-South"},
        {"name": "US-South", "location_type": region_type["id"], "status": active["id"]},
    )
    site = nap.get_or_create(
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
    group = nap.get_or_create("/dcim/rack-groups/", {"name": "Row A"}, {"name": "Row A", "location": site["id"]})
    nap.get_or_create(
        "/dcim/racks/",
        {"name": "A1"},
        {"name": "A1", "location": site["id"], "rack_group": group["id"], "status": active["id"], "u_height": 42},
    )
    device_payload = {
        "location": site["id"],
        "device_type": nap.get_one("/dcim/device-types/", model="C9300-48P")["id"],
        "role": nap.get_one("/extras/roles/", name="Network Device")["id"],
        "platform": nap.get_one("/dcim/platforms/", name="Cisco IOS")["id"],
        "status": active["id"],
    }
    device = nap.get_or_create(
        "/dcim/devices/", {"name": "aus-core-01"}, {"name": "aus-core-01", "serial": "JPE00000001", **device_payload}
    )
    edge = nap.get_or_create("/dcim/devices/", {"name": "aus-edge-01"}, {"name": "aus-edge-01", **device_payload})
    interfaces = {}
    for dev in (device, edge):
        interfaces[dev["name"]] = nap.get_or_create(
            "/dcim/interfaces/",
            {"device": dev["id"], "name": "Ethernet1"},
            {"device": dev["id"], "name": "Ethernet1", "type": "1000base-t", "status": active["id"]},
        )
    vlan = nap.get_or_create(
        "/ipam/vlans/", {"vid": 100, "location": site["id"]}, {"vid": 100, "name": "Users", "status": active["id"]}
    )
    nap.api("PATCH", f"/ipam/vlans/{vlan['id']}/", payload={"locations": [site["id"]]})
    nap.get_or_create(
        "/ipam/ip-addresses/",
        {"address": "10.0.0.1/32"},
        {
            "address": "10.0.0.1/32",
            "namespace": nap.get_one("/ipam/namespaces/", name="Global")["id"],
            "status": active["id"],
            "dns_name": "aus-core-01.example.com",
        },
    )
    nap.get_or_create(
        "/dcim/cables/",
        {"termination_a_id": interfaces["aus-core-01"]["id"]},
        {
            "termination_a_type": "dcim.interface",
            "termination_a_id": interfaces["aus-core-01"]["id"],
            "termination_b_type": "dcim.interface",
            "termination_b_id": interfaces["aus-edge-01"]["id"],
            "status": nap.get_one("/extras/statuses/", name="Connected")["id"],
            "label": "core-edge",
        },
    )
    nap.show(f"/dcim/devices/{device['id']}/")


def verify_ndp_has_the_device(ndp: Instance):
    """Check through the NDP's API that the NAP's inventory arrived, and show the device in the browser."""
    device = ndp.get_one("/dcim/devices/", name="aus-core-01")
    detail = ndp.api("GET", f"/dcim/devices/{device['id']}/", params={"depth": 1})
    location = detail["location"]["name"]
    parent = ndp.api("GET", f"/dcim/locations/{detail['location']['id']}/", params={"depth": 1})["parent"]["name"]
    log(ndp.name, f"Device aus-core-01 is on the NDP at {parent} / {location}")
    ndp.show(f"/dcim/devices/{device['id']}/")
    if location != "Austin" or parent != "US-South":
        raise RuntimeError("the device on the NDP is not under US-South / Austin")
    cables = ndp.api("GET", "/dcim/cables/", params={"label": "core-edge", "depth": 0})["results"]
    vlans = ndp.api("GET", "/ipam/vlans/", params={"vid": 100, "depth": 0})["results"]
    owner = ndp.api("GET", "/extras/object-metadata/", params={"assigned_object_id": device["id"], "depth": 0})
    log(
        ndp.name,
        f"cables: {len(cables)}, VLAN 100: {len(vlans)}, owner metadata: {[m['value'] for m in owner['results']]}",
    )
    if len(cables) != 1 or len(vlans) != 1:
        raise RuntimeError("the NAP's cable or VLAN did not arrive on the NDP")


def ensure_ndp_local_location(ndp: Instance):
    """Create a location on the NDP that no NAP owns; the scoped pull must leave it alone."""
    active = ndp.get_one("/extras/statuses/", name="Active")
    region_type = ndp.get_one("/dcim/location-types/", name="Region")
    return ndp.get_or_create(
        "/dcim/locations/",
        {"name": "NDP-Only"},
        {"name": "NDP-Only", "location_type": region_type["id"], "status": active["id"]},
    )


def retire_the_cable(nap: Instance, ndp: Instance):
    """Delete the cable on the NAP, publish, pull on the NDP and check the tombstone removed it there too."""
    cable = nap.get_one("/dcim/cables/", label="core-edge")
    nap.context.request.delete(f"{nap.base_url}/api/dcim/cables/{cable['id']}/", headers=nap._headers())  # pylint: disable=protected-access
    log(nap.name, "deleted the core-edge cable")
    nap.run_job(TARGET_JOB)
    ndp.run_job(SOURCE_JOB, nautobot_instances=[NAP_INSTANCE])
    if ndp.api("GET", "/dcim/cables/", params={"label": "core-edge", "depth": 0})["results"]:
        raise RuntimeError("the retired cable is still on the NDP")
    if not ndp.api("GET", "/dcim/locations/", params={"name": "NDP-Only", "depth": 0})["results"]:
        raise RuntimeError("the NDP's own location was removed by the pull")
    log(ndp.name, "the retired cable is gone and the NDP-only location survived")


def reset_mongo():
    """Delete every document in the shared collection through the mongo container."""
    log("mongo", "Emptying the shared collection so the run starts from a known state")
    run(
        f"docker exec {MONGO_CONTAINER} mongosh --quiet --eval "
        "\"db.getSiblingDB('nautobot_sync').objects.deleteMany({})\""
    )


def start_stacks(fresh):
    """Bring up the default (NAP) stack and the NDP stack with invoke, optionally destroying them first."""
    if fresh:
        answer = input("--fresh destroys BOTH stacks including their databases. Type 'yes' to continue: ")
        if answer.strip().lower() != "yes":
            sys.exit("aborted")
        run("invoke -f invoke.ndp.yml destroy")
        run("invoke destroy")
    log("stack", "Starting the default stack (NAP-Dev, http://localhost:8080)")
    run("invoke start")
    log("stack", "Starting the NDP stack (NDP, http://localhost:8081)")
    run("invoke -f invoke.ndp.yml start")


def launch_browser(playwright: Playwright, slow_mo, position):
    """Launch one visible Chromium window at the given screen position."""
    return playwright.chromium.launch(
        headless=False,
        slow_mo=slow_mo,
        args=[f"--window-position={position}", "--window-size=1300,940"],
    )


def main(argv=None):
    """Run the whole NDP -> NAP -> NDP loop and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-start", action="store_true", help="assume both stacks are already running")
    parser.add_argument("--fresh", action="store_true", help="destroy both stacks (and their data) before starting")
    parser.add_argument("--keep-mongo", action="store_true", help="do not empty the shared MongoDB collection first")
    parser.add_argument(
        "--keep-nap-objects", action="store_true", help="do not remove the NAP's existing devices and locations"
    )
    parser.add_argument("--slow-mo", type=int, default=250, help="milliseconds to pause between browser actions")
    parser.add_argument("--no-hold", action="store_true", help="close the browsers as soon as the run finishes")
    parser.add_argument("--username", default=os.getenv("NAUTOBOT_SUPERUSER_NAME", "admin"))
    parser.add_argument("--password", default=os.getenv("NAUTOBOT_SUPERUSER_PASSWORD", "admin"))
    args = parser.parse_args(argv)

    if not args.no_start:
        start_stacks(args.fresh)
    log("stack", "Waiting for both instances to answer")
    wait_http(f"{NAP_URL}/health/", 600)
    wait_http(f"{NDP_URL}/health/", 600)
    if not args.keep_mongo:
        reset_mongo()

    with sync_playwright() as playwright:
        ndp_browser = launch_browser(playwright, args.slow_mo, "0,0")
        nap_browser = launch_browser(playwright, args.slow_mo, "1320,0")
        ndp = Instance("NDP", NDP_URL, ndp_browser, args.username, args.password)
        nap = Instance("NAP-Dev", NAP_URL, nap_browser, args.username, args.password)
        try:
            ndp.login()
            nap.login()
            ndp.enable_jobs()
            nap.enable_jobs()

            log("step 1", "NDP loads the catalog it owns into MongoDB")
            ensure_ndp_reference_data(ndp)
            ensure_ndp_local_location(ndp)
            ndp.run_job(TARGET_JOB)

            log("step 2", "NAP syncs the NDP-owned reference data in from MongoDB")
            if not args.keep_nap_objects:
                # Leftover NAP objects may reference tenants or roles the NDP does not own, which would block the
                # reconciliation of the reference data; clear them first so the NAP mirrors the NDP cleanly.
                reset_nap_objects(nap)
            nap.run_job(SOURCE_JOB)

            log("step 3", "NAP loads the inventory it owns into MongoDB")
            create_nap_objects(nap)
            nap.run_job(TARGET_JOB)

            log("step 4", "NDP syncs the NAP-owned inventory in from MongoDB")
            ndp.run_job(SOURCE_JOB, nautobot_instances=[NAP_INSTANCE])
            verify_ndp_has_the_device(ndp)

            log("step 5", "NAP retires the cable; the tombstone removes it from the NDP")
            retire_the_cable(nap, ndp)

            nap.show("/plugins/ssot/")
            log("done", "Full NDP -> NAP -> NDP loop completed, including a tombstoned delete")
            if not args.no_hold:
                input("Press Enter to close the browsers... ")
        finally:
            ndp_browser.close()
            nap_browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
