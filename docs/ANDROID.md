# Building and launching the Android app

The app ships as a **TWA** (Trusted Web Activity): a thin Android shell that
opens `https://<domain>` in Chrome without browser UI. The web app is the app.
There is no `package.json`, no bundler and no build step for `frontend/` — that
property survives packaging, because Bubblewrap is a tool you run on the Mac,
not a dependency of this repo.

Two consequences worth internalising before you start:

* **Shipping a web change needs no Play release.** `deploy.sh` is still the
  release process for everything except the shell itself.
* **The shell is only rebuilt** when the icon, name, theme colour, or target
  API level changes.

---

## 0. The two strings you must choose first

Both are effectively permanent. Nothing below can start until they exist.

| | | Reversible? |
| --- | --- | --- |
| **Domain** | `money.skybolabs.com` ✅ decided | Changing it later breaks every installed app |
| **Application ID** | `com.skybolabs.money` (recommended) | **Never** — it *is* the app's identity on Play |

`com.skybolabs.money` is the right call here, and it happens to be close to
what Bubblewrap would suggest anyway. The usual warning against Bubblewrap's
default — that reversing the host makes your package name hostage to a domain
you might not keep — does not bite when the domain *is* your own lab name. Just
drop the `.twa` suffix Bubblewrap likes to append, and take `com.skybolabs.money`
rather than `com.skybolabs.money.twa`.

The domain was a hard blocker, not a preference: App Links verification resolves
`https://<host>/.well-known/assetlinks.json`, and **an IP literal cannot be
verified**. `deploy/Caddyfile` now serves `money.skybolabs.com` on normal 90-day
certificates, alongside the legacy bare-IP block for the three existing users.
What remains is DNS:

```
money.skybolabs.com.   A   13.234.241.7
```

`skybolabs.com` is already registered with **Amazon Registrar** and served by
**Route 53**, so adding the record is a Route 53 console change, not a registrar
migration — and the hosted-zone fee is already being paid.

The apex already serves a static S3 + CloudFront site. Put the **privacy policy**
and **account-deletion** pages Play requires there rather than on GitHub Pages:
same benefit (a down app server never breaks your Play listing), no new hosting,
and it inherits the security headers that site already sends.

> ### ⚠️ HSTS makes the cert cutover unforgiving
>
> The apex responds with
> `Strict-Transport-Security: max-age=31536000; includeSubDomains`.
>
> `includeSubDomains` means any browser that has loaded `skybolabs.com` will
> **refuse** to talk to `money.skybolabs.com` over plain HTTP, and will refuse a
> bad certificate **with no click-through bypass**. There is no "Proceed anyway"
> for an HSTS-pinned host.
>
> Consequences, all of which bite before you ever reach Play:
> * You cannot test the subdomain over HTTP, even temporarily.
> * If Caddy fails to obtain a cert for `money.skybolabs.com`, the app is hard-
>   down for anyone who has visited the apex — not degraded, *unreachable*.
> * So verify issuance (`journalctl -u caddy -f`) immediately after the DNS
>   record propagates, before telling anyone the hostname exists.
>
> This is a good property once it works — it is exactly the posture a finance
> app wants, and a TWA needs valid TLS regardless. It just removes your safety
> net during the switch.

---

## 1. Toolchain (Mac, one time)

```sh
npm i -g @bubblewrap/cli          # node 25.8 / npm 11.11 already present
brew install --cask android-platform-tools   # gives you adb
bubblewrap doctor
```

On its first run Bubblewrap offers to install its **own** JDK 17 and Android
SDK into `~/.bubblewrap/`; accept. `doctor` then validates that install and is
the command to re-run whenever a build starts failing for no visible reason.
You do not need a system JDK — which is just as well, since
`java` and `keytool` are currently not on PATH on this machine. When a later
step needs `keytool`, use Bubblewrap's copy:

```sh
~/.bubblewrap/jdk/*/Contents/Home/bin/keytool   # path varies by JDK build
```

---

## 2. The signing key

This is the single most destructive thing to lose. **If the upload key is lost
you can ask Google to reset it; if you opt out of Play App Signing and lose the
app signing key, the app can never be updated again by anyone.** Keep Play App
Signing on (it is the default).

```sh
bubblewrap init --manifest=https://money.skybolabs.com/manifest.json
```

`init` prompts for the application ID, display name, colours, and offers to
generate `android.keystore`. Answer the prompts with the values from §0.

`.gitignore` already covers `*.keystore`, `*.jks`, `key.properties`, `*.aab`
and `*.apk` — that was done deliberately before any key existed, because
`deploy.sh` pushes to GitHub. Verify before you generate anything:

```sh
git check-ignore -v android.keystore key.properties   # must print a match
```

Then back the keystore up somewhere that is **not** this repo and not only this
laptop, along with its two passwords.

---

## 3. Build

```sh
bubblewrap build
```

Produces:

* `app-release-bundle.aab` — upload this to Play
* `app-release-signed.apk` — sideload this for local testing

After any change to `manifest.json`, run `bubblewrap update` **then**
`bubblewrap build`. Bubblewrap bakes `themeColor`, `name` and the icons into
the Android project at init time; editing `manifest.json` afterwards does
nothing to an already-generated `android/` directory. `twa-manifest.json` is
the file that actually governs the shell.

---

## 4. Digital Asset Links — the step that silently fails

`frontend/.well-known/assetlinks.json` exists with placeholders. It is served
by Starlette's `StaticFiles` with **no backend change** — verified: the live
app returns `200 application/json` for `/.well-known/assetlinks.json`, because
`StaticFiles` has no dotfile filter and the fail-closed middleware only gates
`/api`.

Put **both** fingerprints in the array:

1. **Play App Signing's** SHA-256 — what installs from Play verify against.
2. **Your upload key's** SHA-256 — what local and internal-test builds verify
   against.

Omitting the second produces the confusing failure mode where the app is clean
in production but shows a URL bar in testing. Play Console → *Setup → App
signing* emits a ready-made JSON block containing both; copy it verbatim over
the placeholder file, then deploy the web app.

Your upload key's fingerprint, if you need it directly:

```sh
keytool -list -v -keystore android.keystore -alias android | grep SHA256
```

**Diagnostic: if you see a Chrome URL bar at the top of the app, verification
failed.** Nothing else about the app will look wrong. Check with:

```sh
adb shell pm get-app-links <applicationId>     # want: verified
```

Common causes, in order of likelihood: the file was not deployed, the
`package_name` does not match, only one fingerprint is listed, or a redirect
sits in front of `/.well-known/`.

---

## 5. Testing

**On a real device** (the only test that proves App Links):

```sh
adb devices                      # enable USB debugging on the phone first
bubblewrap install               # or: adb install -r app-release-signed.apk
```

**On an emulator** — fine for layout, useless for App Links verification unless
the emulator has Play Services and can reach your real domain.

What to actually check:

- [ ] No URL bar (§4)
- [ ] Icon on the home screen is the ₹ mark, not a white box
- [ ] Status bar icons are readable against the blue chrome
- [ ] Rotating does not rotate the app (manifest is `portrait`)
- [ ] Back gesture from the first screen exits rather than stranding you
- [ ] **Airplane mode**: see the warning below
- [ ] Login survives force-quit (the `money_session` cookie lives in Chrome's
      jar on your real origin — this is the thing TWA gets right and a WebView
      wrapper does not)

> **Offline is currently a correctness bug, not a polish item.**
> `main.js` does `api.authMe().catch(() => setAuth("ready"))` — a failed `/me`
> is treated as *authenticated*, so launching offline renders the full UI with
> every account at ₹0. On a finance app that reads as "it deleted my data."
> Fix before any tester sees it: add a fourth `unreachable` auth state. Tracked
> in Phase 2 of the launch plan.

---

## 6. Play Console

### Start the 14-day clock first

A **personal** developer account opened after Nov 2023 must run a **closed test
with 12+ testers for 14 consecutive days** before it can apply for production
access. It is calendar-bound and cannot be compressed, so upload any build —
even a rough one — on day one and let it run while you finish the work.

An **organization** account skips this requirement but needs a D-U-N-S number.
If you have or can get one, it is worth the detour; otherwise start the clock
today.

### Listing — the India personal-loans trap

Play's automated review routes anything mentioning loans, lending or credit
into a policy requiring RBI/NBFC licensing. This app has `lend`/`borrow`/`repay`
features and an in-app credit currency, so in the **title, both descriptions and
every uploaded screenshot**:

* say "split bills and track who owes you"
* never "loan", "lending", "borrow", "credit", "EMI"

Declare **no financial features** in the questionnaire — it is a tracker: no
lending, no payments, no bank connectivity.

### Assets

Already generated by `scripts/make_icons.py`:

| File | Spec | Status |
| --- | --- | --- |
| `store/icon-512-play.png` | 512×512, 32-bit **with** alpha, opaque, full-bleed | ✅ |
| `store/feature-graphic-1024x500.png` | 1024×500, **no** transparency | ✅ |

The requirements are genuinely opposite — Play rejects a 24-bit store icon, but
a store icon with real transparency renders as a black square, and a feature
graphic with any alpha is rejected outright. The script now asserts both.

Still needed by hand: **2–8 phone screenshots**, 16:9 or 9:16, min 320px.

### Data safety

Declare: financial info, photos (receipt attachments in `data/uploads/` — easy
to forget), files, in-app messages, diagnostics. On third-party sharing, chat
text and receipt images go to Anthropic; disclose it regardless of whether the
service-provider carve-out applies. Confirm `COPILOT_LOG_TEXT` is off in
production before answering.

### Mandatory URLs

Host both on the **apex** domain via GitHub Pages, so a down app server never
breaks your listing:

* Privacy policy — must name a **DPDP Act grievance officer** (an Indian legal
  requirement, not a Play one)
* **Account deletion** — Play requires both a web URL and an in-app path, and
  `delete_user()` does not exist yet. See Phase 1.4.

### Also

* App access demo account (`playreview`, seeded with `--sample`) — keep it alive
  forever, reviewers re-check on updates
* Content rating questionnaire, target audience 18+
* AAB only; APKs are not accepted for new apps

---

## 7. Order of operations

Packaging is the **last** step. Three things must be true before §1, and none of
them are Android work:

1. Strangers can create an account (Phase 1.3) — and the uniqueness rebuild in
   Phase 1.2 must land first, or the second signup 409s.
2. A second account actually works end to end (Phase 1.2).
3. AI chat bills something other than a personal Claude subscription
   (Phase 1.5). Serving strangers from a consumer subscription seat is a terms
   problem before it is a capacity one.

Then: **1.1 domain → 1.2 rebuild → 1.5 chat migration → 1.3 signup →
1.4 deletion → this document → Phase 3 billing.**

The closed-test clock runs in parallel from day one.

---

## 8. What is already done

* `manifest.json`: `id`, `orientation: portrait`, `description`, `lang`,
  `categories`, and a `theme_color` that is no longer near-white
* `theme-color` meta tags in `index.html` aligned to the manifest, so the status
  bar does not flash blue → white on launch
* Icons regenerated as 32-bit RGBA, opaque, with a real ₹ glyph —
  **Arial Bold has no U+20B9**, so every previously committed icon was a blank
  tofu box. `scripts/make_icons.py` now hard-fails on a missing glyph instead of
  drawing one
* `icon-192-maskable.png` added; both maskable icons keep the glyph inside the
  80% safe zone
* Play store icon and feature graphic generated with their opposite alpha rules
* `.well-known/assetlinks.json` scaffolded and confirmed to serve
* `.gitignore` covers keystores, `key.properties`, `*.aab`, `*.apk`
* Service worker cache bumped to `money-v3` so installed clients refetch the new
  manifest and icons
