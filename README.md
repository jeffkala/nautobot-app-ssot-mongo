# Nautobot SSoT MongoDB

An app for [Nautobot](https://github.com/nautobot/nautobot).

The term SSoT, or Single Source of Truth, refers to the intention of using Nautobot to consolidate data from disparate Systems of Record to create a single resource for all automation needs. This is done by extending the [Nautobot SSoT framework](https://github.com/nautobot/nautobot-app-ssot) which uses the DiffSync library. This app is built with the capability in mind to import and export data from your desired System of Record.

<p align="center">
  <img src="https://raw.githubusercontent.com/networktocode-llc/nautobot-app-ssot-mongo/develop/docs/images/icon-nautobot-ssot-mongo.png" class="logo" height="200px">
  <br>
  <a href="https://github.com/networktocode-llc/nautobot-app-ssot-mongo/actions"><img src="https://github.com/networktocode-llc/nautobot-app-ssot-mongo/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <a href="https://docs.nautobot.com/projects/nautobot-ssot-mongo/en/latest/"><img src="https://readthedocs.org/projects/nautobot-app-ssot-mongo/badge/"></a>
  <a href="https://pypi.org/project/nautobot-ssot-mongo/"><img src="https://img.shields.io/pypi/v/nautobot-ssot-mongo"></a>
  <a href="https://pypi.org/project/nautobot-ssot-mongo/"><img src="https://img.shields.io/pypi/dm/nautobot-ssot-mongo"></a>
  <br>
  An <a href="https://networktocode.com/nautobot-apps/">App</a> for <a href="https://nautobot.com/">Nautobot</a>.
</p>

## Overview

This app uses a MongoDB collection as a proxy layer between two kinds of Nautobot instance. The **NDP** (Nautobot Data Platform) is the single federated instance: it owns the global catalog (manufacturers, platforms, device types, location types, tenancy, namespaces, VRFs, roles and prefixes) and syncs in the inventory owned by the NAP instances. Each **NAP** (Nautobot Automation Platform) is a local instance: it owns its inventory (locations, rack groups, racks, devices, interfaces, VLANs, IP addresses and cables). Every instance publishes the objects it owns to the collection and mirrors the objects published by the other role, so each NAP works with the NDP's catalog and the NDP holds a consolidated view of every NAP's inventory.

Each Nautobot object is stored as one MongoDB document that carries the name and role of the Nautobot instance that owns it, the Nautobot content type (for example `dcim.devicetype`), a key built from the object's identifiers, a `data` payload with the object's fields, and the versions of Nautobot and this app that wrote it. Related objects are referenced by name using Django's double-underscore notation, so a document is readable and writable by other systems without any knowledge of Nautobot primary keys. Deleted objects become tombstones that expire after a configurable time.

Two SSoT jobs are provided and the configured `nautobot_role` decides what they move. **Nautobot to MongoDB** publishes the objects this instance owns. **MongoDB to Nautobot** mirrors what the other role published: a NAP imports the NDP's catalog, the NDP imports the inventory of the NAPs selected in the job. Every synced object is stamped with the instance that owns it, so a pull reconciles only what it synced in before and adopts matching objects instead of duplicating them. Both jobs support dry runs and record their diffs on the Nautobot SSoT dashboard. Deletions are deferred until last and applied in reverse dependency order so that dependent objects are removed before the objects they reference.

### Screenshots

More screenshots can be found in the [Using the App](https://docs.nautobot.com/projects/nautobot-ssot-mongo/en/latest/user/app_use_cases/) page in the documentation.

## Documentation

Full documentation for this App can be found over on the [Nautobot Docs](https://docs.nautobot.com) website:

- [User Guide](https://docs.nautobot.com/projects/nautobot-ssot-mongo/en/latest/user/app_overview/) - Overview, Using the App, Getting Started.
- [Administrator Guide](https://docs.nautobot.com/projects/nautobot-ssot-mongo/en/latest/admin/install/) - How to Install, Configure, Upgrade, or Uninstall the App.
- [Developer Guide](https://docs.nautobot.com/projects/nautobot-ssot-mongo/en/latest/dev/contributing/) - Extending the App, Code Reference, Contribution Guide.
- [Release Notes / Changelog](https://docs.nautobot.com/projects/nautobot-ssot-mongo/en/latest/admin/release_notes/).
- [Frequently Asked Questions](https://docs.nautobot.com/projects/nautobot-ssot-mongo/en/latest/user/faq/).

### Contributing to the Documentation

You can find all the Markdown source for the App documentation under the [`docs`](https://github.com/networktocode-llc/nautobot-app-ssot-mongo/tree/develop/docs) folder in this repository. For simple edits, a Markdown capable editor is sufficient: clone the repository and edit away.

If you need to view the fully-generated documentation site, you can build it with [MkDocs](https://www.mkdocs.org/). A container hosting the documentation can be started using the `invoke` commands (details in the [Development Environment Guide](https://docs.nautobot.com/projects/nautobot-ssot-mongo/en/latest/dev/dev_environment/#docker-development-environment)) on [http://localhost:8001](http://localhost:8001). Using this container, as your changes to the documentation are saved, they will be automatically rebuilt and any pages currently being viewed will be reloaded in your browser.

Any PRs with fixes or improvements are very welcome!

## Questions

For any questions or comments, please check the [FAQ](https://docs.nautobot.com/projects/nautobot-ssot-mongo/en/latest/user/faq/) first. Feel free to also swing by the [Network to Code Slack](https://networktocode.slack.com/) (channel `#nautobot`), sign up [here](http://slack.networktocode.com/) if you don't have an account.
