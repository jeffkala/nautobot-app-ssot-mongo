# Upgrading the App

Here you will find any steps necessary to upgrade the App in your Nautobot environment.

## Upgrade Guide

When a new release comes out it may be necessary to run a migration of the database to account for any changes in the data models used by this app. Execute the command `nautobot-server post-upgrade` within the runtime environment of your Nautobot installation after updating the `nautobot-ssot-mongo` package via `pip`.

## Upgrading to 0.2

Version 0.2 adds a data migration (the **SSoT MongoDB Owner** metadata type), so `nautobot-server post_upgrade` is
required on every instance. Three behaviour changes affect existing deployments:

- **IP addresses are now owned by the NAPs**, not the NDP. `ipam.ipaddress` documents under the NDP partition are no
  longer read by anyone; remove them once every NAP has published its own:

    ```javascript
    db.objects.deleteMany({nautobot_instance: "NDP", content_type: "ipam.ipaddress"})
    ```

- **Documents gain role and version fields.** The first **Nautobot to MongoDB** run on each owner stamps them on that
  owner's existing documents, so run the push job on every instance once after upgrading. Until a NAP has pushed, the
  NDP still finds it through the "not the NDP and not me" rule used before 0.2.
- **The first pull reconciles only stamped objects.** Objects synced in before 0.2 carry no owner metadata, so the
  first **MongoDB to Nautobot** run after upgrading sees the whole remote set as *create* and adopts each matching
  local object (updating it in place and stamping it). The diff is large but changes no identity; run it with *Dry run*
  first.
