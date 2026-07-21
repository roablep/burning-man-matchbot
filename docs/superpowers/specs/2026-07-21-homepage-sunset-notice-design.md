# Homepage Sunset Notice Design

## Goal

Tell homepage visitors that Matchbot is being sunset in its current format and direct people looking for camps to the community camp directory.

## Placement and content

Add a compact notice as the first element inside the public community homepage's `main.page-wrap`, before the existing mobile intake banner and hero. The notice will say:

> We learned a lot from Matchbot, but we’re sunsetting it—at least in its current format. Looking for a camp? Visit the camp directory.

“camp directory” will link to the Google Sheet provided by the product owner. The external link will open in a new tab and use `rel="noopener noreferrer"`.

## Presentation

Reuse the homepage’s warm, high-contrast visual language. The notice is visually distinct from the regular page content but remains concise and responsive, with no new route, API, configuration, or dependency.

## Verification

Extend the existing public-homepage render test to assert the sunset copy and exact camp-directory URL are included in the response. Run that focused test, then the project lint check.
