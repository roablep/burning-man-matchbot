# Homepage Sunset Notice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a top-of-homepage sunset notice that directs camp seekers to the supplied Google Sheet.

**Architecture:** Keep the public community homepage server-rendered. Add one semantic notice section and scoped CSS in `src/matchbot/public/router.py`, then verify the rendered HTML through the existing FastAPI integration test.

**Tech Stack:** Python 3.12, FastAPI, pytest, inline HTML/CSS.

## Global Constraints

- The notice appears as the first element of the public homepage's `main.page-wrap`.
- Copy must state that Matchbot is being sunset “at least in its current format.”
- The “camp directory” link must use `https://docs.google.com/spreadsheets/d/1pR6kqBTIYLal2GNN6mJ_ueGygaWLo6R2HB5wQhUlBDw/edit?gid=1307777311#gid=1307777311` and open safely in a new tab.
- Do not add routes, settings, dependencies, or unrelated page changes.

---

### Task 1: Render the homepage sunset notice

**Files:**
- Modify: `tests/test_public_community.py:31-60`
- Modify: `src/matchbot/public/router.py:608-716`

**Interfaces:**
- Consumes: `create_app(enable_scheduler=False)` and the existing `/community/` homepage response.
- Produces: Public HTML containing the sunset copy and external camp-directory link.

- [x] **Step 1: Write the failing test**

In `test_community_page_renders`, add these assertions after the existing homepage assertions:

```python
        assert "We learned a lot from Matchbot" in response.text
        assert "we’re sunsetting it—at least in its current format" in response.text
        assert (
            'href="https://docs.google.com/spreadsheets/d/1pR6kqBTIYLal2GNN6mJ_ueGygaWLo6R2HB5wQhUlBDw/edit?gid=1307777311#gid=1307777311"'
            in response.text
        )
        assert 'target="_blank" rel="noopener noreferrer"' in response.text
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_public_community.py::test_community_page_renders -v`

Expected: FAIL because the current homepage HTML has no sunset notice copy.

- [x] **Step 3: Write minimal implementation**

In `_HOME_CSS`, add:

```css
.sunset-notice {
  margin: 0 0 18px;
  padding: 14px 16px;
  border: 1.5px solid rgba(255, 146, 0, 0.55);
  border-radius: 16px;
  background: rgba(255, 246, 226, 0.95);
  color: #221e21;
  font-size: 14px;
  line-height: 1.6;
}
.sunset-notice p { margin: 0; }
.sunset-notice a { color: #000; font-weight: 700; }
```

As the first child of `<main class="page-wrap">` in `_HOME_BODY`, add:

```html
    <section class="sunset-notice" aria-label="Matchbot sunset notice">
      <p>We learned a lot from Matchbot, but we’re sunsetting it—at least in its current format. Looking for a camp? Visit the <a href="https://docs.google.com/spreadsheets/d/1pR6kqBTIYLal2GNN6mJ_ueGygaWLo6R2HB5wQhUlBDw/edit?gid=1307777311#gid=1307777311" target="_blank" rel="noopener noreferrer">camp directory</a>.</p>
    </section>
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_public_community.py::test_community_page_renders -v`

Expected: PASS.

- [x] **Step 5: Run affected quality checks**

Run: `uv run ruff check src/matchbot/public/router.py tests/test_public_community.py && uv run pytest tests/test_public_community.py -v`

Expected: Ruff reports no errors and all tests in `tests/test_public_community.py` pass.
