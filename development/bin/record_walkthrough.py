#!/usr/bin/env python
"""Record a captioned video walkthrough of the app's UI across the NDP and NAP development stacks.

The script drives one headless Chromium session through both Nautobot stacks of the development environment,
narrates every step with a caption drawn on the page (the recording has no audio) and saves the session as a
`.webm` file. Watch it to review how the app's elements look, behave and interact:

- the NDP (`invoke -f invoke.ndp.yml start`, http://localhost:8081): the ownership rules list, filters, detail
  page and edit form, the guard on owner changes, the Transfer Ownership job (dry run), the Nautobot to MongoDB
  job publishing the catalog and the rules, the SSoT dashboard, the owner stamp on a synced object, the REST API
  and GraphQL;
- the NAP (`invoke start`, http://localhost:8080): the same rules as a read-only mirror, the rejected edit, the
  MongoDB to Nautobot job (dry run) refreshing the rules first, the owner stamp on a catalog object, and the
  sync history.

Jobs that write are limited to the NDP's publish, which is idempotent; everything else is a dry run or a read.
The two stacks must be running and migrated, and the three app jobs enabled on the NDP (the two sync jobs on the
NAP). The NDP half runs first and the NAP half second, so the single browser session only ever holds one login:
both stacks sit on localhost, where cookies ignore the port.

    invoke record-walkthrough              # or: poetry run python development/bin/record_walkthrough.py

The video lands in `development/recordings/` (ignored by git) unless `--output-dir` says otherwise.
"""

import argparse
import os
import shutil
import sys
import time
from datetime import datetime

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_OUTPUT_DIR = os.path.join(REPO_ROOT, "development", "recordings")
NAP_URL = "http://localhost:8080"
NDP_URL = "http://localhost:8081"
RULES_PATH = "/plugins/ssot-mongo/ownership-rules/"
SOURCE_JOB = "nautobot_ssot_mongo.jobs.MongoDataSource"
TARGET_JOB = "nautobot_ssot_mongo.jobs.MongoDataTarget"
TRANSFER_JOB = "nautobot_ssot_mongo.jobs.TransferOwnership"
JOB_TIMEOUT = 300
VIEWPORT = {"width": 1440, "height": 900}

CAPTION_SCRIPT = """
([title, text]) => {
    let bar = document.getElementById("walkthrough-caption");
    if (!bar) {
        bar = document.createElement("div");
        bar.id = "walkthrough-caption";
        bar.style.cssText = [
            "position:fixed", "left:0", "right:0", "bottom:0", "z-index:2147483647",
            "background:rgba(20,24,33,0.92)", "color:#fff", "padding:14px 24px",
            "font:16px/1.45 -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif",
            "box-shadow:0 -2px 12px rgba(0,0,0,0.4)", "pointer-events:none",
        ].join(";");
        document.body.appendChild(bar);
    }
    bar.innerHTML = "<div style='font-weight:700;font-size:17px;margin-bottom:4px;color:#8ec5ff'></div><div></div>";
    bar.children[0].textContent = title;
    bar.children[1].textContent = text;
}
"""

CARD_SCRIPT = """
([title, lines]) => {
    const card = document.createElement("div");
    card.id = "walkthrough-card";
    card.style.cssText = [
        "position:fixed", "inset:0", "z-index:2147483647", "background:#141821", "color:#fff",
        "display:flex", "flex-direction:column", "justify-content:center", "align-items:center",
        "font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif",
        "padding:60px", "text-align:center", "pointer-events:none",
    ].join(";");
    const heading = document.createElement("div");
    heading.style.cssText = "font-size:40px;font-weight:700;margin-bottom:28px;color:#8ec5ff";
    heading.textContent = title;
    card.appendChild(heading);
    for (const line of lines) {
        const item = document.createElement("div");
        item.style.cssText = "font-size:21px;line-height:1.6;max-width:900px";
        item.textContent = line;
        card.appendChild(item);
    }
    document.body.appendChild(card);
}
"""


def log(message):
    """Print one narrated line on the terminal."""
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


class Walkthrough:
    """One recorded browser session, narrated with captions and chapter cards."""

    def __init__(self, page: Page, pace_ms: int):
        """Keep the page and the pause applied after every caption."""
        self.page = page
        self.pace_ms = pace_ms
        self.base_url = ""
        self.name = ""

    # -- narration -------------------------------------------------------------------------------------------------

    def caption(self, title, text, hold=1.0):
        """Draw the caption bar on the current page and hold it for `hold` times the pace."""
        log(f"{self.name}: {title} - {text}")
        self.page.evaluate(CAPTION_SCRIPT, [title, text])
        self.page.wait_for_timeout(int(self.pace_ms * hold))

    def card(self, title, lines, hold=2.0):
        """Cover the page with a chapter card for `hold` times the pace."""
        log(f"{self.name}: == {title}")
        self.page.evaluate(CARD_SCRIPT, [title, lines])
        self.page.wait_for_timeout(int(self.pace_ms * hold))
        self.page.evaluate("() => document.getElementById('walkthrough-card')?.remove()")

    def show(self, path, title, text, hold=1.0):
        """Open a path of the current instance, then caption it."""
        self.page.goto(f"{self.base_url}{path}")
        self.page.wait_for_load_state("networkidle")
        self.caption(title, text, hold)

    def scroll(self, pixels=500):
        """Scroll the page down a little so that content below the fold is seen."""
        self.page.mouse.wheel(0, pixels)
        self.page.wait_for_timeout(int(self.pace_ms * 0.5))

    # -- session ---------------------------------------------------------------------------------------------------

    def use(self, name, base_url):
        """Switch narration to one instance."""
        self.name = name
        self.base_url = base_url

    def login(self, username, password):
        """Log in through the login form."""
        self.page.goto(f"{self.base_url}/login/")
        self.page.fill("#id_username", username)
        self.page.fill("#id_password", password)
        self.caption("Login", f"Signing in to the {self.name} at {self.base_url} as {username}.", 0.6)
        self.page.click("button[type=submit]")
        self.page.wait_for_load_state("networkidle")
        if "/login/" in self.page.url:
            raise RuntimeError(f"{self.name}: login failed for {username}; pass --username/--password")

    def api(self, path, **params):
        """Return the decoded JSON of a GET on the REST API, using the browser session."""
        response = self.page.context.request.get(f"{self.base_url}/api{path}", params=params)
        if not response.ok:
            raise RuntimeError(f"{self.name}: GET {path} -> {response.status} {response.text()[:300]}")
        return response.json()

    def first(self, path, **params):
        """Return the first object matching `params`, or None."""
        results = self.api(path, depth=0, limit=1, **params)["results"]
        return results[0] if results else None

    # -- jobs ------------------------------------------------------------------------------------------------------

    def run_job(self, class_path, title, text, dryrun=True, fill=None):
        """Open a job's run form, caption it, submit it and wait on the job result page until it finishes."""
        self.show(f"/extras/jobs/{class_path}/run/", title, text, 1.2)
        self.page.set_checked("#id_dryrun", dryrun)
        if fill:
            fill(self.page)
        self.caption(title, "Submitting the job with 'Run Job Now'.", 0.6)
        self.page.click("#id__run")
        try:
            self.page.wait_for_url("**/extras/job-results/**")
        except PlaywrightTimeoutError as error:
            errors = " | ".join(t.strip() for t in self.page.locator(".alert, .invalid-feedback").all_inner_texts())
            raise RuntimeError(f"{self.name}: the job form stayed on {self.page.url}: {errors}") from error
        job_result_id = self.page.url.rstrip("/").split("/")[-1]
        self.caption(title, "The job result page refreshes itself while the worker runs the job.", 0.8)
        deadline = time.monotonic() + JOB_TIMEOUT
        status = "PENDING"
        while time.monotonic() < deadline:
            status = self.api(f"/extras/job-results/{job_result_id}/", depth=0)["status"]["value"]
            if status in ("SUCCESS", "FAILURE", "REVOKED"):
                break
            self.page.wait_for_timeout(2000)
        self.page.reload()
        self.page.wait_for_load_state("networkidle")
        self.caption(title, f"Finished with status {status}. The log below lists what the job did.", 1.4)
        self.scroll(600)
        self.caption(title, "Job log entries, newest at the bottom.", 1.2)
        return status


# -- scenes ----------------------------------------------------------------------------------------------------------


def ndp_scenes(walk: Walkthrough, username, password):  # pylint: disable=too-many-statements
    """Everything the NDP shows: the rules, their guards, the jobs, the dashboard, the API."""
    walk.use("NDP", NDP_URL)
    walk.page.goto(f"{NDP_URL}/login/")
    walk.card(
        "Nautobot SSoT MongoDB: the NDP",
        [
            "The NDP (Nautobot Data Platform) owns the catalog and syncs in the inventory every NAP publishes.",
            "This part shows the ownership rules, the guards on them, the jobs and the owner stamps.",
        ],
    )
    walk.login(username, password)
    walk.show("/", "Home", "The NDP's home page. The app lives under Apps > MongoDB SSoT.", 0.8)

    walk.show(
        RULES_PATH,
        "Ownership Rules",
        "One rule per federated content type: owner role, model, enabled flag and sync order. "
        "The list is in sync order, the order both jobs process the types in.",
        1.6,
    )
    walk.scroll(400)
    walk.caption("Ownership Rules", "Catalog types belong to the NDP (blue), inventory types to the NAPs (teal).", 1.0)
    walk.show(f"{RULES_PATH}?owner_role=NAP", "Filtering", "Filtered on owner role = NAP: the inventory types.", 1.2)
    walk.show(f"{RULES_PATH}?q=tenan", "Searching", "The search box matches the app label or the model name.", 1.0)

    vlan = walk.first(RULES_PATH, content_type="ipam.vlan")
    rule_url = f"{RULES_PATH}{vlan['id']}/"
    walk.show(
        rule_url,
        "Rule detail",
        "Left: the rule's fields. Right: where the rule is maintained, which on the NDP points at the "
        "Transfer Ownership job for owner changes. Standard tabs: Advanced, Notes, Change Log.",
        1.8,
    )
    walk.show(
        f"{rule_url}changelog/", "Change log", "Every edit of a rule is change-logged like any Nautobot object.", 1.0
    )

    walk.show(
        f"{rule_url}edit/", "Editing a rule", "Unchecking Enabled leaves VLANs out of both sync jobs everywhere.", 1.2
    )
    walk.page.set_checked("#id_enabled", False)
    walk.page.click("button[name=_update]")
    walk.page.wait_for_load_state("networkidle")
    walk.caption("Editing a rule", "Saved. The next Nautobot to MongoDB run publishes the rules to every NAP.", 1.2)

    walk.show(f"{rule_url}edit/", "The owner guard", "Trying to change the owner through the form.", 1.0)
    walk.page.locator("#id_owner_role").select_option("NDP", force=True)
    walk.page.click("button[name=_update]")
    walk.page.wait_for_load_state("networkidle")
    walk.caption(
        "The owner guard",
        "Rejected: changing an owner moves objects between instances, so it is a job, not an edit.",
        1.8,
    )
    walk.page.locator("#id_owner_role").select_option("NAP", force=True)
    walk.page.set_checked("#id_enabled", True)
    walk.page.click("button[name=_update]")
    walk.page.wait_for_load_state("networkidle")
    walk.caption("Editing a rule", "Re-enabled with the owner left as it was.", 0.8)

    walk.show("/extras/jobs/", "Jobs", "The app's three jobs are grouped under MongoDB SSoT.", 1.0)
    walk.scroll(300)

    def pick_vlan(page):
        # The rule picker loads its options from the API as you type, so give the select its option directly.
        page.evaluate(
            """([value, label]) => {
                const select = document.getElementById("id_rule");
                const option = new Option(label, value, true, true);
                select.add(option);
                select.dispatchEvent(new Event("change", {bubbles: true}));
            }""",
            [vlan["id"], "ipam.vlan"],
        )

    walk.run_job(
        TRANSFER_JOB,
        "Transfer Ownership (dry run)",
        "Hands a type to the other role. Dry run reports the direction and what the NDP would release or retire.",
        dryrun=True,
        fill=pick_vlan,
    )
    walk.run_job(
        TARGET_JOB,
        "Nautobot to MongoDB",
        "The NDP publishes its catalog. Since this release it publishes the ownership rules first, "
        "so every NAP mirrors them before its next job.",
        dryrun=False,
    )

    walk.show(
        "/plugins/ssot/",
        "SSoT dashboard",
        "The nautobot-ssot dashboard lists the app as a data source and a data target.",
        1.2,
    )
    walk.show(
        f"/plugins/ssot/data-target/{TARGET_JOB}/",
        "Data target detail",
        "Configuration summary (role, instance, synced objects from the rules) and the data mappings.",
        1.6,
    )
    walk.scroll(500)
    walk.show(
        "/plugins/ssot/history/",
        "Sync history",
        "Every run with its diff summary; click one for the per-object log.",
        1.0,
    )

    device = walk.first("/dcim/devices/")
    if device:
        walk.show(
            f"/dcim/devices/{device['id']}/",
            "A synced object",
            "A device pulled from a NAP. The Object Metadata tab holds the SSoT MongoDB Owner stamp.",
            1.0,
        )
        walk.show(
            f"/dcim/devices/{device['id']}/object-metadata/",
            "Owner stamp",
            "The stamp names the NAP that published the object; pulls reconcile only stamped objects.",
            1.6,
        )

    walk.show(
        f"/api{RULES_PATH}?limit=3",
        "REST API",
        "The rules are exposed at /api/plugins/ssot-mongo/ownership-rules/ with the content type as app_label.model.",
        1.6,
    )
    walk.scroll(400)
    query = "{ ownership_rules { content_type { app_label model } owner_role enabled sync_order } }"
    walk.show("/graphql/", "GraphQL", "The rules also have a GraphQL type; typing a query into GraphiQL.", 0.8)
    try:
        editor = walk.page.locator(".graphiql-query-editor .cm-content, .graphiql-query-editor .CodeMirror").first
        editor.click(timeout=5000)
        walk.page.keyboard.press("Meta+A")
        walk.page.keyboard.press("Control+A")
        walk.page.keyboard.type(query, delay=15)
        walk.page.locator(".graphiql-execute-button, .execute-button").first.click(timeout=3000)
        walk.page.wait_for_timeout(1500)
        walk.caption("GraphQL", "The query on the left, its result on the right.", 1.4)
    except PlaywrightTimeoutError:
        walk.caption("GraphQL", "GraphiQL could not be driven here; the type is listed in the schema docs.", 0.8)


def nap_scenes(walk: Walkthrough, username, password):
    """Everything the NAP shows: the mirrored rules, the rejected edit, the refreshing pull, the stamps."""
    walk.use("NAP", NAP_URL)
    walk.page.goto(f"{NAP_URL}/login/")
    walk.card(
        "The NAP (NAP-Dev)",
        [
            "A NAP (Nautobot Automation Platform) owns its inventory and mirrors the NDP's catalog.",
            "It also mirrors the ownership rules: they are refreshed from MongoDB before each sync job.",
        ],
    )
    walk.login(username, password)
    walk.show(RULES_PATH, "Ownership Rules on a NAP", "The same rules, mirrored from the NDP's partition.", 1.2)
    rule = walk.first(RULES_PATH, content_type="ipam.vlan")
    rule_url = f"{RULES_PATH}{rule['id']}/"
    walk.show(rule_url, "Rule detail on a NAP", "The notice explains that the rules are read-only here.", 1.4)
    walk.show(f"{rule_url}edit/", "Editing on a NAP", "Trying to disable the rule on the NAP.", 0.8)
    walk.page.set_checked("#id_enabled", False)
    walk.page.click("button[name=_update]")
    walk.page.wait_for_load_state("networkidle")
    walk.caption("Editing on a NAP", "Rejected: edit the rule on the NDP; the sync jobs bring it here.", 1.6)

    walk.run_job(
        SOURCE_JOB,
        "MongoDB to Nautobot (dry run)",
        "The NAP pulls the catalog. The job first refreshes the ownership rules from the NDP's partition, "
        "then computes the diff of the NDP-owned types.",
        dryrun=True,
    )

    manufacturer = walk.first("/dcim/manufacturers/")
    if manufacturer:
        walk.show(
            f"/dcim/manufacturers/{manufacturer['id']}/object-metadata/",
            "Owner stamp on a catalog object",
            "A manufacturer pulled from the NDP carries the NDP as owner; a pull never touches unstamped objects.",
            1.6,
        )
    walk.show(f"/plugins/ssot/data-source/{SOURCE_JOB}/", "Data source detail", "The NAP's view of the pull job.", 1.2)
    walk.show("/plugins/ssot/history/", "Sync history", "Recent runs on this NAP.", 1.0)
    walk.card(
        "End of walkthrough",
        [
            "Rules are defined on the NDP, published with its catalog, mirrored by every NAP before each job.",
            "Owner changes go through Transfer Ownership; each instance settles its own copies on its next run.",
        ],
        hold=2.5,
    )


def main(argv=None):
    """Record the walkthrough and print where the video landed."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--username", default=os.getenv("NAUTOBOT_SUPERUSER_NAME", "admin"))
    parser.add_argument("--password", default=os.getenv("NAUTOBOT_SUPERUSER_PASSWORD", "admin"))
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--pace", type=int, default=2200, help="Base pause after each caption, in milliseconds.")
    parser.add_argument("--headed", action="store_true", help="Show the browser window while recording.")
    args = parser.parse_args(argv)

    os.makedirs(args.output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = os.path.join(args.output_dir, f"walkthrough-{stamp}.webm")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        context = browser.new_context(viewport=VIEWPORT, record_video_dir=args.output_dir, record_video_size=VIEWPORT)
        # The development stacks run the Django debug toolbar, which would cover the right of every page.
        context.add_init_script(
            "try { localStorage.setItem('djdt.show', 'false'); } catch (error) {}"
            "document.addEventListener('DOMContentLoaded', () => {"
            " const style = document.createElement('style');"
            " style.textContent = '#djDebugRoot, #djDebug { display: none !important; }';"
            " document.head.appendChild(style); });"
        )
        page = context.new_page()
        walk = Walkthrough(page, args.pace)
        try:
            ndp_scenes(walk, args.username, args.password)
            nap_scenes(walk, args.username, args.password)
        finally:
            video = page.video
            context.close()
            browser.close()
            if video is not None:
                shutil.move(video.path(), target)
                log(f"Saved {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
