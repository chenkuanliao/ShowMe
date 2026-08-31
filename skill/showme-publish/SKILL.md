---
name: showme-publish
description: Create, publish, or update HTML reports, dashboards, visual summaries, and webpage artifacts on a configured personal ShowMe server, then always return the remote link. Use whenever the user asks for a report or artifact in HTML/webpage form, asks to host/share/publish one, or invokes or pins this skill. Publishing and returning the URL are automatic when this skill is used; do not wait for a separate request for a link. Do not use when the user explicitly requests local-only output or a different hosting destination.
---

# Publish with ShowMe

Create a self-contained webpage in a disposable ShowMe workspace, upload it through the installed `showme` CLI, remove the workspace after success, and return the published URL. The ShowMe server is the source of truth unless the user asks to retain a local project.

## Completion contract

When this skill is invoked or pinned, publishing is part of completing the task. After creating or revising the HTML, upload it without asking whether the user wants it published. The final response must contain the clickable ShowMe URL. A local file path alone is not a completed result.

The only exception is an explicit request for local-only output or another hosting destination. If ShowMe is unavailable, report the precise blocker instead of silently falling back to a local-only handoff.

## Workflow

1. Check the integration before doing substantial webpage work:

   ```bash
   showme doctor
   ```

   If the command is missing or unconfigured, explain the exact error and ask the user to run the ShowMe repository installer. Never read or print `~/.config/showme/config.json`; the CLI owns its API token.

2. Create a managed disposable workspace and use the printed path as the artifact directory:

   ```bash
   showme workspace create
   ```

   Put the entry point at `index.html`. Keep CSS and ordinary JavaScript inside that file when practical; place images and other necessary assets in relative subdirectories.

   ```text
   report/
   ├── index.html
   └── assets/
       └── chart.png
   ```

   Use only relative asset references. Do not include symlinks. Treat generated HTML as trusted active content; do not copy unknown scripts or webpage bundles into it.

3. Verify that `index.html` exists and referenced local assets are inside the directory. Perform any task-appropriate local checks before publishing.

4. Publish after the artifact is ready:

   ```bash
   showme publish /absolute/path/to/report --title "Descriptive title" --json
   ```

   Public is the default. When the user requests private access, use:

   ```bash
   showme publish /absolute/path/to/report --title "Descriptive title" --private --json
   ```

   Let ShowMe generate the passcode unless the user supplied one. Do not invent or expose API tokens.

5. After a successful publish, remove only the managed workspace:

   ```bash
   showme workspace clean /absolute/path/to/workspace
   ```

   If verification or publishing fails, retain the workspace and report its path so the work can be recovered. Never clean an ordinary user-provided directory.

6. Return the clickable `url` from the JSON response prominently in the final answer, even if the user did not separately ask for a link. For a private page, also return the one-time generated `passcode` and tell the user to save it.

When the user explicitly asks to keep local source, create or use an ordinary project directory instead of a managed workspace. Publishing never deletes an ordinary directory.

## Update an existing page

When the user wants to revise a previously published report, download its canonical source into a managed workspace:

```bash
showme workspace pull PAGE_ID
```

Edit and verify the printed directory, then replace the page content instead of publishing a new page:

```bash
showme update PAGE_ID /absolute/path/to/report --json
```

A full ShowMe page URL may replace `PAGE_ID`. Use `--title "New title"` only when the title should also change. This command preserves the page ID, URL, visibility, passcode, and archive state.

Use the page link or ID supplied in the conversation. If it is unavailable, run `showme list` and identify the page by title; do not guess when multiple pages could match. Return the unchanged URL after updating.

After a successful update, clean the managed workspace. Retain and report it if download, editing, verification, or update fails.

For a local project the user wants to retain, use `showme pull PAGE_ID DIRECTORY`. This writes `.showme.json`, allowing later updates from inside the project with `showme update .`. ShowMe metadata files are not uploaded as page assets.

## Page management

Use the CLI for requested changes:

```bash
showme visibility PAGE_ID public
showme visibility PAGE_ID private
showme update PAGE_ID /absolute/path/to/report
showme archive PAGE_ID
showme restore PAGE_ID
showme delete PAGE_ID
```

Switching to private without `--passcode` returns a newly generated passcode. Switching to public removes the old passcode. Treat deletion as permanent and obtain clear user intent before running it.
