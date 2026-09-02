# Frontend

A React SPA (Vite, `react-router-dom`) that exercises the real API end to end
— no in-page mock logic, unlike the reference demo it was built from. Source:
`apps/web/src/`.

## Built from a real reference, not from scratch

[`.agents/controlplane_demo.html`](../../.agents/controlplane_demo.html) was a pure frontend mock with no backend — a
single HTML file with in-page JS simulating detectors and fusion, used as the
UI/UX and visual reference. This app matches its **flow and visual language**
(the color tokens, card/badge/trace-chip styling, the chat + pipeline-trace
layout) exactly, while replacing every piece of in-page simulated logic with
real calls to the FastAPI backend. `apps/web/src/index.css` reuses the demo's
CSS custom properties and class names directly.

## Routes

```jsx
<Route path="/login" element={<Login />} />
<Route path="/signup" element={<Signup />} />
<Route element={<RequireAuth><Layout /></RequireAuth>}>
  <Route path="/" element={<OrgPolicy />} />
  <Route path="/agents" element={<AgentsList />} />
  <Route path="/agents/:agentId/policy" element={<AgentPolicy />} />
  <Route path="/agents/:agentId/chat" element={<Chat />} />
  <Route path="/agents/:agentId/learning" element={<LearningPlane />} />
</Route>
```

`RequireAuth` is a simple redirect guard checking for a stored token — not a
security boundary (the real one is server-side, per
[multi-tenancy & auth](multi-tenancy-and-auth.md)), just a UX convenience so
an unauthenticated visitor lands on `/login` instead of a broken page.

## Screens

| Screen | What it does |
|---|---|
| `Login.jsx` / `Signup.jsx` | Auth forms; on success, store the JWT + org info in `localStorage`, redirect to `/`. `Login.jsx` also has a "View demo" button — calls `POST /auth/demo-login` (no credentials), lands on the singleton Demo Org |
| `OrgPolicy.jsx` | YAML editor for the org layer; save triggers `POST /policies/org` and shows how many agents got recompiled |
| `AgentsList.jsx` | Create/list agents; each links to its policy screen and chat demo |
| `AgentPolicy.jsx` | YAML editor for one agent's layer; compile shows the resolved bundle JSON and any clamp events |
| `Chat.jsx` | The demo's four preset scenarios (injection, card number, canary leak, toxic+PII) plus free-text input, `POST /check`, renders the pipeline trace |
| `LearningPlane.jsx` | Four required pages (`.agents/Learning_Plane_PRD_Draft.md` §0), one tab each: **Audit Ledger** (KPIs + row browser + hash-chain verify), **Reviewer Queue** (KPIs + approve/reject), **Calibration + Metrics** (FP/FN cards + calibration chart, computed live from the mock dataset — no run button, no waiting), **Policy Versions** (version list + promote, shadow-deploy picker + diff table, calibration → new-policy-version confirm flow) |

Policy editors work in YAML text (parsed client-side with `js-yaml`, matching
the demo's own editing experience) even though the API itself speaks JSON —
the textarea is friendlier for a human editing a layer than a JSON form would
be, and it's exactly how the reference demo did it.

## The trace panel

`components/Trace.jsx` renders whatever stages `Chat.jsx` builds from the raw
API response — it doesn't recompute anything client-side (unlike the demo,
which simulated the whole pipeline in JS). `buildStages()` in `Chat.jsx`
splits the output ledger row's `contributing_signals` back into T0 and T1
groups by their `source` prefix (`t0_`/`t1_`) purely for display grouping —
the actual decision was already made server-side by the time this runs.

```js
const t0 = output.contributing_signals.filter(s => s.source.startsWith("t0_"));
const t1 = output.contributing_signals.filter(s => s.source.startsWith("t1_"));
```

## A real bug found by driving the app in a real browser, not by reading the code

End-to-end verification used a headless Chromium script that walked
signup → org policy → agent creation → policy compile → all four chat presets
→ a free-text prompt. The first run silently skipped two of the four presets.
The cause was in the *test script*, not the app: it clicked the next preset
before the previous `/check` request had resolved, and `Chat.jsx`'s
`send()` correctly no-ops while a request is already in flight —

```js
async function send(prompt) {
  if (!prompt.trim() || sending) return;   // this guard is correct; the test driver wasn't waiting for it
  ...
}
```

— so the fix was in the test driver (wait for the input to re-enable before
firing the next action), not in the app. Worth recording because it's the
kind of finding that's easy to misdiagnose as an app bug when it's actually
proof the app's own concurrency guard works.

## Verifying the learning plane screen without Playwright or `chromium-cli`

Neither was available in the environment this was built in. Verification used
a from-scratch ~100-line Python driver talking directly to Chrome DevTools
Protocol over a raw websocket (`google-chrome --headless=new
--remote-debugging-port=...`, `Runtime.evaluate` / `Page.navigate` /
`Page.captureScreenshot`), including the standard workaround for React's
controlled inputs (`fill` uses the native `HTMLInputElement`/`HTMLTextAreaElement`
value setter plus a dispatched `input` event, since directly assigning
`el.value` doesn't fire React's `onChange`). Confirmed zero console errors
across the full flow: signup → org/agent policy compile → all four Learning
Plane tabs → a real calibration → new-policy-version confirmation (which also
caught a real, correctly-behaving rejection: picking a threshold that
collided with `output_pii_review_threshold` was rejected by `compile()`'s
existing guard, and the UI surfaced that rejection instead of crashing).

## What's out of scope

- **Streaming chat responses.** The chat screen waits for the full `/check`
  response; there's no token-by-token rendering.
- **Mobile-responsive layout.** Built and tested at desktop widths only.
- **A charting library.** The calibration chart (`components/CalibrationChart.jsx`)
  is a small inline-SVG line chart — two series over ~13 points didn't
  justify adding a dependency the project doesn't otherwise have.
