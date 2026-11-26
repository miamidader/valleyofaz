# Valley of AZ Feed

A single page that gathers Phoenix-area news, filtered by topic and city. A free GitHub Action refreshes `data/items.json` four times a day (5 AM, 11 AM, 4 PM, 9 PM Arizona time), and GitHub Pages serves the page. Pull down at the top of the page to reload.

Sources live in `feeds.json`. Add any RSS or Atom link with an `id`, `name`, `url` and `group`, or set `"enabled": false` to pause one. The Sources section at the bottom of the page shows which feeds worked on the last run.
